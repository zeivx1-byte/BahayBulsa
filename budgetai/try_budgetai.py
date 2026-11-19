"""Try BudgetAI on four sample households and print the results.

Run from the BudgetAI_Model folder with the virtual environment active:
    python try_budgetai.py          run all four households
    python try_budgetai.py 2        run only household 2
    python try_budgetai.py 2 --json also print the full result as JSON
    python try_budgetai.py --save   also save everything (with JSON) to results/
"""
import sys
import textwrap
from datetime import datetime

import config
from model.budgeting_ai import BudgetAI, BudgetAIError

RESULTS_DIR = config.PROJECT_ROOT / "results"

# Never crash just because the terminal cannot display a character.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(errors="replace")


class Tee:
    """Writes the same text to the screen and to a file."""

    def __init__(self, *streams):
        self.streams = streams

    def write(self, text):
        for stream in self.streams:
            stream.write(text)

    def flush(self):
        for stream in self.streams:
            stream.flush()

SAMPLES = [
    {
        "title": "1. Santos family - middle income with credit card debt",
        "profile": {
            "incomes": [
                {"name": "Juan's salary", "monthly_amount": 32000},
                {"name": "Maria's salary", "monthly_amount": 18000},
            ],
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
            "debts": [
                {"name": "Credit card", "balance": 65000, "minimum_payment": 2600, "annual_interest_rate_percent": 36},
            ],
            "emergency_fund_balance": 10000,
            "household_size": 4,
            "location": "Quezon City",
        },
    },
    {
        "title": "2. Reyes household - income does not cover essentials",
        "profile": {
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
            "debts": [
                {"name": "Online lending app", "balance": 8000, "minimum_payment": 1600, "annual_interest_rate_percent": 120},
            ],
            "household_size": 3,
            "location": "Caloocan",
        },
    },
    {
        "title": "3. Ana - freelance designer with irregular income",
        "profile": {
            "incomes": [
                {"name": "Freelance projects", "monthly_amount": 38000, "regular": False, "minimum_monthly_amount": 22000},
            ],
            "expenses": [
                {"name": "Room rent", "category": "housing", "monthly_amount": 9000},
                {"name": "Food", "category": "food", "monthly_amount": 6000},
                {"name": "Electricity and internet", "category": "utilities", "monthly_amount": 2500},
                {"name": "Transportation", "category": "transportation", "monthly_amount": 1500},
                {"name": "PhilHealth and Pag-IBIG (voluntary)", "category": "insurance_contributions", "monthly_amount": 700},
                {"name": "Design software subscription", "category": "subscriptions", "monthly_amount": 1200,
                 "kind": "essential"},
                {"name": "Coffee shops and dining", "category": "dining_out", "monthly_amount": 3500},
                {"name": "Shopping", "category": "shopping", "monthly_amount": 2000},
            ],
            "savings_goals": [
                {"name": "New laptop for work", "target_amount": 60000, "current_amount": 15000, "months_left": 8},
            ],
            "emergency_fund_balance": 20000,
            "household_size": 1,
            "location": "Cebu City",
            "notes": "Needs the software for work.",
        },
    },
    {
        "title": "4. Incomplete profile - only income is known",
        "profile": {"incomes": [{"name": "Salary", "monthly_amount": 25000}]},
    },
]


def peso(amount: float, currency: str) -> str:
    return f"{currency} {amount:,.2f}"


SOURCE_LABEL = {"required": "required", "rule": "budget rule"}


def show(title: str, result) -> None:
    cur = result.currency
    f = result.facts
    a = result.advice
    wrap = textwrap.TextWrapper(width=96, initial_indent="  ", subsequent_indent="  ")
    print("=" * 100)
    print(title)
    print("=" * 100)
    print(f"Facts (Python): planning income {peso(f.planning_income, cur)} | required spending "
          f"{peso(f.required_spending, cur)} | left after required {peso(f.balance_after_required, cur)} "
          f"| situation: {f.situation.upper()}" + (" (PARTIAL PLAN)" if result.is_partial else ""))
    print(f"\nBudget built by Python ({len(result.budget)} lines; P = priority level):")
    print(f"  {'P':<2} {'Name':<38} {'Amount':>16}  {'Why':<12}")
    for line in sorted(result.budget, key=lambda b: b.priority):
        note = f"  (needs {peso(line.needed_amount, cur)})" if line.amount + 0.005 < line.needed_amount else ""
        print(f"  {line.priority:<2} {line.name[:38]:<38} {peso(line.amount, cur):>16}  {SOURCE_LABEL[line.source]:<12}{note}")
        if line.source == "rule" or note:
            print(f"     -> {line.reason}")
    print(f"  {'':<41} {'-' * 16}")
    print(f"  {'Total budget':<41} {peso(result.total_allocated, cur):>16}")
    print(f"  {'Not allocated':<41} {peso(result.unallocated, cur):>16}")
    if result.unfunded > 0:
        print(f"  {'Required spending NOT covered':<41} {peso(result.unfunded, cur):>16}")
    if result.rule_notes:
        print("\nPython notes:")
        for item in result.rule_notes:
            print(f"  - {item}")
    print("\nSummary (AI):")
    print(wrap.fill(a.summary))
    print("\nSavings advice (AI):")
    print(wrap.fill(a.savings_advice))
    sections = (("Action steps for this month (Python)", result.action_steps), ("Warnings (AI)", a.warnings),
                ("Questions for the user (AI)", a.missing_information))
    for label, items in sections:
        if items:
            print(f"\n{label}:")
            for item in items:
                print(f"  - {item}")
    if a.spending_flags:
        print("\nSuggested cuts (AI for essentials, Python for non-essentials):")
        for s in a.spending_flags:
            print(f"  - {s.expense_name}: {peso(s.current_amount, cur)} -> {peso(s.suggested_amount, cur)}. {s.reason}")
    print("\nPython checks:")
    for check in result.checks:
        print(f"  {check}")
    print(f"\n({result.model}, {result.attempts} AI call(s), {result.seconds} s)\n")


def main() -> None:
    args = [a for a in sys.argv[1:] if a not in ("--json", "--save")]
    if args and not (args[0].isdigit() and 1 <= int(args[0]) <= len(SAMPLES)):
        print(f"Choose a household number from 1 to {len(SAMPLES)}, e.g.  python try_budgetai.py 2")
        sys.exit(1)
    chosen = [SAMPLES[int(args[0]) - 1]] if args else SAMPLES

    save_file = None
    if "--save" in sys.argv:
        RESULTS_DIR.mkdir(exist_ok=True)
        save_path = RESULTS_DIR / f"try_budgetai_{datetime.now():%Y%m%d_%H%M%S}.txt"
        save_file = open(save_path, "w", encoding="utf-8")
        sys.stdout = Tee(sys.__stdout__, save_file)

    try:
        ai = BudgetAI()
    except BudgetAIError as exc:
        print(f"[FAIL] {exc}")
        sys.exit(1)

    for sample in chosen:
        print(f"Asking BudgetAI about: {sample['title']} ...")
        try:
            result = ai.generate_budget(sample["profile"])
        except BudgetAIError as exc:
            print(f"[FAIL] {exc}\n")
            continue
        show(sample["title"], result)
        if "--json" in sys.argv:
            print(result.model_dump_json(indent=2))
        elif save_file:
            save_file.write(result.model_dump_json(indent=2) + "\n\n")

    if save_file:
        sys.stdout = sys.__stdout__
        save_file.close()
        print(f"Saved to {save_path}")


if __name__ == "__main__":
    main()
