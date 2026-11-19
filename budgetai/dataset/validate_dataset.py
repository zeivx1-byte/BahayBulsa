"""Validates the BudgetAI dataset.

Run from the BudgetAI_Model folder (virtual environment active):
    python dataset/validate_dataset.py

What it checks, for every scenario:
1. The profile is valid and the rules version matches the current rules.
2. The expected budget is reproduced exactly by the current planner.
3. Independent checks: simple arithmetic written separately from the
   budgeting code re-checks the totals and every budget rule, including the
   Phase 5 rules: whole-centavo amounts whose displayed lines add up to the
   displayed total, debts on hold (minimum above the balance), labelled
   partial plans, and irregular income without a low month.
4. Golden scenarios: amounts worked out by hand match exactly.
And for the dataset as a whole: unique IDs, no household in both files
(no leakage), and enough scenarios of every type in both files.
"""
import collections
import hashlib
from decimal import Decimal
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from budgeting_engine.allocation import RULES_VERSION  # noqa: E402
from budgeting_engine.calculations import CATEGORY_KIND  # noqa: E402
from budgeting_engine.planner import make_plan  # noqa: E402
from model.schemas import FinancialProfile  # noqa: E402

TRAIN_FILE = ROOT / "dataset" / "budgeting_examples.jsonl"
TEST_FILE = ROOT / "dataset" / "test_scenarios.jsonl"
CENT = 0.011            # allowed rounding difference: just over one centavo
MIN_PER_TYPE = {"train": 15, "test": 5}
REQUIRED_TYPES = ["low_income", "middle_income", "high_income", "debt_heavy", "irregular_income",
                  "high_fixed_expenses", "aggressive_savings", "deficit", "spending_habits", "incomplete", "edge_case"]
HIGH_INTEREST = 20.0


def load(path: Path) -> list:
    records = []
    with open(path, encoding="utf-8") as f:
        for number, line in enumerate(f, 1):
            if line.strip():
                try:
                    records.append(json.loads(line))
                except json.JSONDecodeError as exc:
                    raise ValueError(f"{path.name} line {number} is not valid JSON: {exc}") from None
    return records


# ------------------------------------------------------------------ independent arithmetic
def _kind(expense):
    return expense.get("kind") or CATEGORY_KIND[expense["category"]]


def independent_checks(rec: dict) -> list:
    """Re-check one expected budget with simple arithmetic. Returns a list of problems."""
    p, e = rec["profile"], rec["expected"]
    f, lines = e["facts"], e["budget"]
    expenses, debts, goals = p.get("expenses", []), p.get("debts", []), p.get("savings_goals", [])
    balance = p.get("emergency_fund_balance", 0)
    problems = []

    def problem(text):
        problems.append(f"{rec['id']}: {text}")

    planning = sum(i["minimum_monthly_amount"] if not i.get("regular", True) and i.get("minimum_monthly_amount") is not None
                   else i["monthly_amount"] for i in p["incomes"])
    held = [d for d in debts if d["minimum_payment"] > d["balance"]]       # inconsistent: not budgeted
    paid = [d for d in debts if d["minimum_payment"] <= d["balance"]]
    required = (sum(x["monthly_amount"] for x in expenses if _kind(x) in ("essential", "obligation"))
                + sum(d["minimum_payment"] for d in paid))
    deficit = required > planning
    total = sum(line["amount"] for line in lines)

    if abs(planning - f["planning_income"]) > CENT:
        problem(f"planning income should be {planning:.2f}, not {f['planning_income']:.2f}")
    if abs(required - f["required_spending"]) > CENT:
        problem(f"required spending should be {required:.2f}, not {f['required_spending']:.2f}")
    if deficit != f["is_deficit"]:
        problem("deficit flag is wrong")
    if abs(total - e["total_allocated"]) > CENT:
        problem("total_allocated does not equal the sum of the budget lines")

    # Money policy: whole centavos, and the amounts people see add up exactly.
    # Profiles are stored already rounded, so the checks above use what the planner used.
    def cents(value):
        return abs(value * 100 - round(value * 100)) < 1e-6
    stored = ([i[k] for i in p["incomes"] for k in ("monthly_amount", "minimum_monthly_amount") if i.get(k) is not None]
              + [x["monthly_amount"] for x in expenses] + [d[k] for d in debts for k in ("balance", "minimum_payment")]
              + [g[k] for g in goals for k in ("target_amount", "current_amount") if k in g] + [balance])
    if not all(cents(v) for v in stored):
        problem("a profile amount is not a whole number of centavos (store profiles rounded to centavos)")
    amounts = [l[k] for l in lines for k in ("amount", "needed_amount")]
    if not all(cents(v) for v in amounts + [e["total_allocated"], e["unallocated"], e["unfunded"]]):
        problem("an amount is not a whole number of centavos")
    shown = sum(Decimal(f"{l['amount']:.2f}") for l in lines)
    if shown != Decimal(f"{e['total_allocated']:.2f}"):
        problem(f"the lines shown add up to {shown}, but the total shown is {e['total_allocated']:.2f}")
    if Decimal(f"{f['planning_income']:.2f}") - Decimal(f"{e['total_allocated']:.2f}") != Decimal(f"{e['unallocated']:.2f}"):
        problem("planning income minus the total shown is not the unallocated amount shown")

    # Rule: never above income, never negative, never above what a line needs
    if total > planning + CENT:
        problem(f"budget {total:.2f} is above planning income {planning:.2f}")
    for line in lines:
        if line["amount"] < 0:
            problem(f"negative amount on '{line['name']}'")
        if line["priority"] in (1, 2, 6) and line["amount"] > line["needed_amount"] + CENT:
            problem(f"'{line['name']}' gets more than it needs")

    # Rule 1: one required line per essential/obligation expense and per minimum payment
    wanted = collections.Counter({})
    for x in expenses:
        if _kind(x) in ("essential", "obligation"):
            wanted[(x["name"], x["monthly_amount"])] += 1
    for d in paid:
        if d["minimum_payment"] > 0:
            wanted[(f"{d['name']} (minimum payment)", d["minimum_payment"])] += 1
    got = collections.Counter((l["name"], l["needed_amount"]) for l in lines if l["source"] == "required")
    if got != wanted:
        problem("required lines do not match the essential/obligation expenses and minimum payments")

    # Debts on hold: not budgeted, explained in a note and asked about
    for d in held:
        if not any(d["name"] in m for m in e["missing_data"]):
            problem(f"'{d['name']}' has a minimum above its balance, but the user is not asked to confirm it")
        if not any(d["name"] in n and "not budgeted" in n for n in e["rule_notes"]):
            problem(f"'{d['name']}' has a minimum above its balance, but no note says it is not budgeted")
    # Irregular income without a low month: say that the stated amount was used
    for i in p["incomes"]:
        if not i.get("regular", True) and i.get("minimum_monthly_amount") is None:
            if not any(i["name"] in n and "stated monthly amount" in n for n in e["rule_notes"]):
                problem(f"'{i['name']}' has no low month, but no note says the stated monthly amount was used")
            if not any(i["name"] in m and "stated monthly amount" in m for m in e["missing_data"]):
                problem(f"'{i['name']}' has no low month, but the missing data does not say the stated amount was used")

    if not expenses:
        if lines and any(l["source"] == "rule" for l in lines):
            problem("no expenses are listed, but savings or wants were budgeted")
        if not e["missing_data"]:
            problem("no expenses are listed, but nothing is reported missing")
        if lines:   # a partial plan: it must say so everywhere
            if not any(n.startswith("Partial plan") for n in e["rule_notes"]):
                problem("partial plan (debts but no expenses) without a 'Partial plan' note")
            if not all("Partial plan" in l["reason"] for l in lines):
                problem("a line in a partial plan does not say it is part of a partial plan")
            if not e["action_steps"] or not e["action_steps"][-1].startswith("List your monthly expenses"):
                problem("a partial plan must end by asking for the monthly expenses")
    elif not deficit:
        # Every required line in full, and every peso assigned
        for l in lines:
            if l["source"] == "required" and abs(l["amount"] - l["needed_amount"]) > CENT:
                problem(f"required line '{l['name']}' is not fully funded")
        if abs(total - planning) > CENT:
            problem(f"{planning - total:.2f} of income is left unassigned")
        # Rule 3: the future gets at least its share of the money left
        available = planning - required
        months = balance / required if required > 0 else None
        high_debt = any((d.get("annual_interest_rate_percent") is None or d["annual_interest_rate_percent"] > HIGH_INTEREST)
                        and d["balance"] > d["minimum_payment"] for d in debts)
        if (months is not None and months < 1) or high_debt:
            share = .60
        elif (months is not None and months < 3) or any(g.get("months_left") and g["target_amount"] > g.get("current_amount", 0)
                                                         for g in goals):
            share = .40
        else:
            share = .20
        wants_now = sum(x["monthly_amount"] for x in expenses if _kind(x) == "non_essential")
        future_min = available - min(wants_now, (1 - share) * available)
        future = sum(l["amount"] for l in lines if l["source"] == "rule" and 3 <= l["priority"] <= 5)
        if future + CENT < future_min:
            problem(f"savings and debt payoff get {future:.2f}, less than the rule's minimum {future_min:.2f}")
    else:
        # Rule 2 (deficit): nothing for savings or wants; priority 1 before priority 2; all income used
        if any(l["source"] == "rule" and l["amount"] > 0 for l in lines):
            problem("money went to savings or wants during a deficit")
        p1_full = all(abs(l["amount"] - l["needed_amount"]) <= CENT for l in lines if l["priority"] == 1)
        if not p1_full and any(l["amount"] > 0 for l in lines if l["priority"] == 2):
            problem("obligations were funded before essentials were covered")
        if abs(total - planning) > CENT:
            problem("in a deficit, all income should be used")
        if abs(e["unfunded"] - (required - planning)) > 2 * CENT:
            problem(f"unfunded should be {required - planning:.2f}, not {e['unfunded']:.2f}")

    # Caps: emergency fund up to 6 months, extra debt payments up to the balance, goals up to the target
    for l in lines:
        if l["name"] == "Emergency fund" and l["amount"] > 6 * required - balance + CENT:
            problem("emergency fund goes beyond 6 months of required spending")
        if l["name"].startswith("Extra payment: "):
            debt = next((d for d in debts if d["name"] == l["name"][len("Extra payment: "):]), None)
            if debt is None or l["amount"] > debt["balance"] - debt["minimum_payment"] + CENT:
                problem(f"'{l['name']}' pays more than is owed")
        if l["name"].startswith("Savings goal: "):
            goal = next((g for g in goals if g["name"] == l["name"][len("Savings goal: "):]), None)
            if goal is None or l["amount"] > goal["target_amount"] - goal.get("current_amount", 0) + CENT:
                problem(f"'{l['name']}' saves more than the goal needs")

    # Action steps only use amounts that exist in the budget or facts
    known = {round(l["amount"], 2) for l in lines}
    wants = [l for l in lines if l["priority"] == 6]
    known |= {round(sum(l["amount"] for l in wants), 2), round(sum(l["needed_amount"] for l in wants), 2),
              round(f["shortfall"], 2)}
    known |= {round(l["needed_amount"], 2) for l in lines}                    # "the minimum is ..."
    known |= {round(d[k], 2) for d in held for k in ("minimum_payment", "balance")}
    for text in re.findall(r"PHP ([\d,]+\.\d\d)", " ".join(e["action_steps"])):
        if round(float(text.replace(",", "")), 2) not in known:
            problem(f"action step mentions PHP {text}, which is not in the budget")
    return problems


def golden_checks(rec: dict) -> list:
    """Amounts worked out by hand must match the expected budget exactly."""
    problems = []
    amounts = {l["name"]: l["amount"] for l in rec["expected"]["budget"]}
    if not rec["hand_checked"] and amounts:
        problems.append(f"{rec['id']}: expected an empty budget")
    for name, value in rec["hand_checked"].items():
        if name not in amounts:
            problems.append(f"{rec['id']}: hand-checked line '{name}' is missing")
        elif abs(amounts[name] - value) > 0.005:
            problems.append(f"{rec['id']}: '{name}' is {amounts[name]:.2f}, hand-checked {value:.2f}")
    return problems


# ------------------------------------------------------------------ the whole dataset
def validate(train_path: Path = TRAIN_FILE, test_path: Path = TEST_FILE) -> dict:
    report = {"problems": [], "counts": {}, "golden": 0, "checked": 0}
    problems = report["problems"]
    files = {"train": load(train_path), "test": load(test_path)}
    seen_ids, seen_profiles = set(), {}

    for split, records in files.items():
        for rec in records:
            report["checked"] += 1
            rid = rec.get("id", "?")
            if rid in seen_ids:
                problems.append(f"{rid}: duplicate id")
            seen_ids.add(rid)
            if rec.get("split") != split:
                problems.append(f"{rid}: split says '{rec.get('split')}' but it is in the {split} file")
            if rec.get("rules_version") != RULES_VERSION:
                problems.append(f"{rid}: made with rules {rec.get('rules_version')}, current rules are {RULES_VERSION}; "
                                "regenerate the dataset")
            try:
                FinancialProfile.model_validate(rec["profile"])
            except Exception as exc:
                problems.append(f"{rid}: invalid profile ({str(exc).splitlines()[0]})")
                continue
            key = hashlib.sha256(json.dumps(rec["profile"], sort_keys=True).encode()).hexdigest()
            if key in seen_profiles:
                problems.append(f"{rid}: same household as {seen_profiles[key]} (duplicate or leakage)")
            seen_profiles[key] = rid
            fresh = make_plan(rec["profile"]).model_dump(mode="json", exclude={"rules_version"})
            if json.dumps(fresh, sort_keys=True) != json.dumps(rec["expected"], sort_keys=True):
                problems.append(f"{rid}: expected budget differs from what the current rules produce")
            problems.extend(independent_checks(rec))
            if "hand_checked" in rec:
                report["golden"] += 1
                problems.extend(golden_checks(rec))
        report["counts"][split] = collections.Counter(r["scenario_type"] for r in records)

    for split, minimum in MIN_PER_TYPE.items():
        for kind in REQUIRED_TYPES:
            minimum_here = minimum if kind not in ("incomplete", "edge_case") else max(3, minimum // 3)
            if report["counts"].get(split, {}).get(kind, 0) < minimum_here:
                problems.append(f"only {report['counts'][split].get(kind, 0)} '{kind}' scenarios in {split} "
                                f"(need {minimum_here})")
    situations = collections.Counter(r["expected"]["facts"]["situation"] for r in files["test"])
    for situation in ("stable", "tight", "deficit", "incomplete"):
        if situations[situation] < 3:
            problems.append(f"only {situations[situation]} '{situation}' scenarios in the test file (need 3)")
    report["situations"] = {split: dict(collections.Counter(r["expected"]["facts"]["situation"] for r in recs))
                            for split, recs in files.items()}
    return report


def main() -> None:
    print("BudgetAI - dataset validation\n")
    try:
        report = validate()
    except FileNotFoundError as exc:
        print(f"[FAIL] {exc.filename} not found. Run first:  python dataset/generate_dataset.py")
        sys.exit(1)
    except ValueError as exc:
        print(f"[FAIL] {exc}")
        sys.exit(1)

    for split in ("train", "test"):
        total = sum(report["counts"][split].values())
        print(f"{split:<5} {total:>4} scenarios  |  situations: {report['situations'][split]}")
    print("\nScenarios per type (train / test):")
    for kind in REQUIRED_TYPES + ["golden"]:
        print(f"  {kind:<22} {report['counts']['train'].get(kind, 0):>4} / {report['counts']['test'].get(kind, 0):<4}")

    checks = [
        "profiles valid and rules version current",
        "expected budgets reproduced exactly by the planner",
        "independent arithmetic: totals, never above income, every peso assigned",
        "independent rules: required first, deficit order, future share, caps, action steps",
        "money policy: whole centavos; the lines shown add up to the total shown",
        "data problems: debts on hold, partial plans, irregular income without a low month",
        f"golden scenarios match hand-worked amounts ({report['golden']})",
        "unique ids, no household in both files, enough of every type",
    ]
    print()
    if report["problems"]:
        for text in report["problems"][:30]:
            print(f"[FAIL] {text}")
        if len(report["problems"]) > 30:
            print(f"... and {len(report['problems']) - 30} more problems")
        print(f"\nDataset validation FAILED: {len(report['problems'])} problem(s) in {report['checked']} scenarios.")
        sys.exit(1)
    for text in checks:
        print(f"[OK]   {text}")
    print(f"\nDataset validation passed: {report['checked']} scenarios checked.")


if __name__ == "__main__":
    main()
