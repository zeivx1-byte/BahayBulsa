"""Shows one dataset scenario in a readable way.

Run from the BudgetAI_Model folder (virtual environment active):
    python dataset/show_scenario.py BA-0001
    python dataset/show_scenario.py BA-G02      (a hand-checked golden scenario)
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FILES = [ROOT / "dataset" / "budgeting_examples.jsonl", ROOT / "dataset" / "test_scenarios.jsonl"]


def find(scenario_id: str):
    for path in FILES:
        with open(path, encoding="utf-8") as f:
            for line in f:
                if f'"id": "{scenario_id}"' in line:
                    return json.loads(line)
    return None


def main() -> None:
    if len(sys.argv) != 2:
        print("Usage:  python dataset/show_scenario.py BA-0001")
        sys.exit(1)
    rec = find(sys.argv[1].upper())
    if rec is None:
        print(f"No scenario with id {sys.argv[1]}. Ids look like BA-0001 or BA-G01.")
        sys.exit(1)
    p, e = rec["profile"], rec["expected"]
    f = e["facts"]
    print(f"{rec['id']}  ({rec['split']}, {rec['scenario_type']})\n{rec['title']}\n")
    print("Income:")
    for i in p["incomes"]:
        low = f" (lowest month {i['minimum_monthly_amount']:,.0f})" if i.get("minimum_monthly_amount") else ""
        print(f"  {i['name']}: {i['monthly_amount']:,.0f}{low}")
    print(f"\nFacts: planning income {f['planning_income']:,.2f} | required {f['required_spending']:,.2f} | "
          f"left {f['balance_after_required']:,.2f} | emergency fund {f['emergency_fund_months'] or 0:.2f} months | "
          f"situation {f['situation']}")
    print("\nExpected budget:")
    for line in sorted(e["budget"], key=lambda l: l["priority"]):
        short = f"  (needs {line['needed_amount']:,.2f})" if line["amount"] + 0.005 < line["needed_amount"] else ""
        print(f"  P{line['priority']} {line['name'][:40]:<40} {line['amount']:>12,.2f}{short}")
    print(f"  {'Total':<43} {e['total_allocated']:>12,.2f}")
    for label, items in (("Rule notes", e["rule_notes"]), ("Action steps", e["action_steps"]),
                         ("Missing information", e["missing_data"])):
        if items:
            print(f"\n{label}:")
            for item in items:
                print(f"  - {item}")
    if "hand_checked" in rec:
        print(f"\nHand-checked amounts: {rec['hand_checked'] or 'empty budget'}")


if __name__ == "__main__":
    main()
