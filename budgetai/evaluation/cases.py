"""Evaluation case files (Phase 6): definitions, writing and checking.

Two evaluation sets live here, separate from the training data and never used
as prompt examples:

- regression_cases.jsonl  named failures that must not come back (Santos,
                          Reyes) and the Phase 5 data problems (a debt held for
                          clarification, a partial plan, irregular income on its
                          stated amount).
- challenge_cases.jsonl   synthetic households informed by public aggregate
                          statistics (PSA FIES, BSP CFS); see challenge_data.py.

Each record has the same shape as the dataset (id, profile, expected plan from
the rules) plus what a person checked by hand ("hand_checked"), the facts the
explanation must communicate, and for challenge cases the source and
assumptions. Rebuild the files with:

    python -m evaluation.cases --write
and check them (also done by the tests) with:
    python -m evaluation.cases --check
"""
import argparse
import json
import sys
from pathlib import Path

from budgeting_engine.planner import make_plan
from dataset import validate_dataset as val

HERE = Path(__file__).resolve().parent
REGRESSION_FILE = HERE / "regression_cases.jsonl"
CHALLENGE_FILE = HERE / "challenge_cases.jsonl"

# ------------------------------------------------------------------ regression cases
SANTOS = {
    "incomes": [{"name": "Juan's salary", "monthly_amount": 32000}, {"name": "Maria's salary", "monthly_amount": 18000}],
    "expenses": [
        {"name": "Rent", "category": "housing", "monthly_amount": 12000},
        {"name": "Groceries and palengke", "category": "food", "monthly_amount": 11000},
        {"name": "Meralco", "category": "utilities", "monthly_amount": 3200},
        {"name": "Water", "category": "utilities", "monthly_amount": 600},
        {"name": "Internet", "category": "utilities", "monthly_amount": 1699},
        {"name": "Fare and gas", "category": "transportation", "monthly_amount": 3500},
        {"name": "Tuition installment", "category": "education", "monthly_amount": 3000},
        {"name": "Padala to parents", "category": "family_support", "monthly_amount": 2000},
        {"name": "Dining out and food delivery", "category": "dining_out", "monthly_amount": 4000},
        {"name": "Netflix and Spotify", "category": "subscriptions", "monthly_amount": 700},
        {"name": "Online shopping", "category": "shopping", "monthly_amount": 3000},
    ],
    "debts": [{"name": "Credit card", "balance": 65000, "minimum_payment": 2600, "annual_interest_rate_percent": 36}],
    "emergency_fund_balance": 10000, "household_size": 4, "location": "Quezon City",
}
REYES = {
    "incomes": [{"name": "Salary", "monthly_amount": 16000}],
    "expenses": [
        {"name": "Rent", "category": "housing", "monthly_amount": 5500},
        {"name": "Food", "category": "food", "monthly_amount": 7500},
        {"name": "Electricity and water", "category": "utilities", "monthly_amount": 2200},
        {"name": "Mobile load", "category": "utilities", "monthly_amount": 300},
        {"name": "Jeep and tricycle fare", "category": "transportation", "monthly_amount": 2000},
        {"name": "Padala sa probinsya", "category": "family_support", "monthly_amount": 1500},
        {"name": "Snacks and fast food", "category": "dining_out", "monthly_amount": 800},
    ],
    "debts": [{"name": "Online lending app", "balance": 8000, "minimum_payment": 1600, "annual_interest_rate_percent": 120}],
    "household_size": 3, "location": "Caloocan",
}

REGRESSION_CASES = [
    {
        "id": "R-SANTOS", "title": "Santos family: stable, credit card debt, emergency fund under 1 month",
        "focus": "Phase 4 failure: the AI told them to save another PHP 1,000 without saying where it comes from.",
        "profile": SANTOS,
        # 50000 - required 39599 = 10401 left. Fund under 1 month + 36% debt: at least 60% goes to the
        # future, so wants = min(7700, 40% x 10401 = 4160.40); future = 6240.60, split half/half.
        "hand_checked": {"lines": {"Emergency fund": 3120.30, "Extra payment: Credit card": 3120.30},
                         "facts": {"required_spending": 39599, "balance_after_required": 10401}},
        "regression_checks": ["unplanned_extra_saving"],
        "must_communicate": [],
    },
    {
        "id": "R-REYES", "title": "Reyes household: PHP 4,600 shortfall, nothing for non-essentials",
        "focus": "Phase 4 failure: the AI said the earlier PHP 800 of snacks was 'set aside' in the new budget.",
        "profile": REYES,
        # Required = 17500 essentials + 1500 padala + 1600 loan = 20600; 16000 income -> 4600 short.
        # Essentials take all 16000, so padala, the loan and the snacks get 0 (snacks are not a line at all).
        "hand_checked": {"lines": {"Padala sa probinsya": 0, "Online lending app (minimum payment)": 0},
                         "absent_lines": ["Snacks and fast food"],
                         "facts": {"required_spending": 20600, "shortfall": 4600}, "total_allocated": 16000},
        "regression_checks": ["set_aside_not_in_plan"],
        "must_communicate": ["deficit_shortfall"],
    },
    {
        "id": "R-HELD-DEBT", "title": "Debt minimum (PHP 500) above its balance (PHP 100)",
        "focus": "Phase 5: the payment is held until the household confirms the amounts.",
        "profile": {"incomes": [{"name": "Salary", "monthly_amount": 30000}],
                    "expenses": [{"name": "Rent", "category": "housing", "monthly_amount": 10000},
                                 {"name": "Food", "category": "food", "monthly_amount": 8000}],
                    "debts": [{"name": "Store credit", "balance": 100, "minimum_payment": 500,
                               "annual_interest_rate_percent": 24}],
                    "household_size": 2, "location": "Pasig"},
        "hand_checked": {"absent_lines": ["Store credit (minimum payment)", "Extra payment: Store credit"],
                         "facts": {"minimum_debt_payments": 0, "required_spending": 18000}},
        "regression_checks": ["held_debt_paid"],
        "must_communicate": ["held_debt:Store credit"],
    },
    {
        "id": "R-PARTIAL", "title": "Partial plan: a known loan payment, no expenses listed",
        "focus": "Phase 5: only the PHP 1,500 minimum is budgeted; the plan and explanation must say partial plan.",
        "profile": {"incomes": [{"name": "Salary", "monthly_amount": 25000}],
                    "debts": [{"name": "SSS salary loan", "balance": 20000, "minimum_payment": 1500,
                               "annual_interest_rate_percent": 10}],
                    "household_size": 3, "location": "Davao City"},
        "hand_checked": {"lines": {"SSS salary loan (minimum payment)": 1500}, "unallocated": 23500},
        "regression_checks": ["partial_called_no_budget"],
        "must_communicate": ["partial_plan"],
    },
    {
        "id": "R-IRREGULAR-STATED", "title": "Irregular income with no low month reported",
        "focus": "Phase 5: planning uses the stated PHP 30,000; the explanation must not claim a low month was used.",
        "profile": {"incomes": [{"name": "Freelance projects", "monthly_amount": 30000, "regular": False}],
                    "expenses": [{"name": "Rent", "category": "housing", "monthly_amount": 9000},
                                 {"name": "Food", "category": "food", "monthly_amount": 7000}],
                    "household_size": 1, "location": "Cebu City"},
        "hand_checked": {"facts": {"planning_income": 30000, "required_spending": 16000}},
        "regression_checks": ["false_low_month"],
        "must_communicate": ["irregular_stated_amount"],
    },
]


# ------------------------------------------------------------------ building and checking
def build_record(case: dict, set_name: str) -> dict:
    plan = make_plan(case["profile"])
    rec = {"id": case["id"], "set": set_name, "title": case["title"], "focus": case.get("focus", ""),
           "rules_version": plan.rules_version, "profile": case["profile"],
           "expected": plan.model_dump(mode="json", exclude={"rules_version"}),
           "hand_checked": case["hand_checked"], "must_communicate": case.get("must_communicate", []),
           "regression_checks": case.get("regression_checks", [])}
    for key in ("source", "assumptions", "region", "gap"):
        if key in case:
            rec[key] = case[key]
    return rec


def hand_check_problems(rec: dict) -> list:
    """Compare what a person worked out by hand with the expected plan."""
    e, hand, problems = rec["expected"], rec["hand_checked"], []
    lines = {l["name"]: l["amount"] for l in e["budget"]}
    for name, value in hand.get("lines", {}).items():
        if name not in lines:
            problems.append(f"{rec['id']}: hand-checked line '{name}' is missing")
        elif abs(lines[name] - value) > 0.005:
            problems.append(f"{rec['id']}: '{name}' is {lines[name]:.2f}, hand-checked {value:.2f}")
    for name in hand.get("absent_lines", []):
        if name in lines:
            problems.append(f"{rec['id']}: line '{name}' should not be in the budget")
    for field, value in hand.get("facts", {}).items():
        if abs(e["facts"][field] - value) > 0.005:
            problems.append(f"{rec['id']}: fact {field} is {e['facts'][field]}, hand-checked {value}")
    for field in ("total_allocated", "unallocated", "unfunded"):
        if field in hand and abs(e[field] - hand[field]) > 0.005:
            problems.append(f"{rec['id']}: {field} is {e[field]}, hand-checked {hand[field]}")
    return problems


def check_records(records: list) -> list:
    """Problems in an evaluation file: rules reproduction, independent checks, hand checks."""
    problems, seen = [], set()
    for rec in records:
        if rec["id"] in seen:
            problems.append(f"duplicate id {rec['id']}")
        seen.add(rec["id"])
        plan = make_plan(rec["profile"])
        if plan.rules_version != rec["rules_version"]:
            problems.append(f"{rec['id']}: rules version {rec['rules_version']} is not the current {plan.rules_version}")
        if plan.model_dump(mode="json", exclude={"rules_version"}) != rec["expected"]:
            problems.append(f"{rec['id']}: the planner no longer reproduces the expected plan")
        problems += [f"{rec['id']}: {p}" for p in val.independent_checks(rec)]
        problems += hand_check_problems(rec)
    return problems


def load(path: Path) -> list:
    with open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def write(path: Path, records: list) -> None:
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        for rec in records:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def all_definitions() -> dict:
    from evaluation.challenge_data import CHALLENGE_CASES
    return {REGRESSION_FILE: ("regression", REGRESSION_CASES), CHALLENGE_FILE: ("challenge", CHALLENGE_CASES)}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--write", action="store_true", help="rebuild the evaluation case files")
    parser.add_argument("--check", action="store_true", help="check the evaluation case files")
    args = parser.parse_args()
    for path, (set_name, cases) in all_definitions().items():
        if not cases:
            continue
        if args.write:
            write(path, [build_record(c, set_name) for c in cases])
            print(f"wrote {len(cases)} cases to {path.name}")
        if args.check or not args.write:
            problems = check_records(load(path))
            for p in problems:
                print("[FAIL]", p)
            print(f"{path.name}: {len(load(path))} cases, {len(problems)} problem(s)")
            if problems:
                sys.exit(1)


if __name__ == "__main__":
    main()
