"""Money calculations: exact, deterministic, no AI.

Every amount is a whole number of centavos: inputs are rounded when the
profile is checked (model/schemas.py) and money() rounds every result the
same way, so the lines shown always add up to the total shown.
"""
from model.schemas import Debt, Expense, FinancialProfile, FinancialSummary, to_centavos

# How each category is treated when the user doesn't say.
# essential   = needed to live and work
# obligation  = committed payments that are hard to stop
# non_essential = can be reduced or paused if money is tight
CATEGORY_KIND = {
    "housing": "essential",
    "food": "essential",
    "utilities": "essential",
    "transportation": "essential",
    "health": "essential",
    "education": "obligation",
    "insurance_contributions": "obligation",
    "family_support": "obligation",
    "personal_care": "non_essential",
    "shopping": "non_essential",
    "dining_out": "non_essential",
    "entertainment": "non_essential",
    "subscriptions": "non_essential",
    "other": "non_essential",
}

# Categories that usually cost the same every month.
FIXED_CATEGORIES = {"housing", "education", "insurance_contributions", "family_support", "subscriptions"}

# "tight" = less than this share of planning income is left after required spending.
# Only used to set the tone of the AI's explanation.
TIGHT_SHARE = 0.10


def money(value: float) -> float:
    """Round to the nearest centavo, halves up (the money policy in model/schemas.py).

    Python's round() rounds 2.675 down to 2.67 (binary floating point); this
    uses the decimal value as written, so 2.675 becomes 2.68 everywhere.
    """
    return to_centavos(value) + 0.0   # + 0.0 turns -0.0 into 0.0


def plain(value: float) -> str:
    """Write a number the way JSON does, with no thousands separators: 4000 or 1699.5.

    Used in AI prompts. If a prompt says "4,000", the model may copy the comma
    into its JSON answer, where it cuts the number off at 4.
    """
    text = f"{value:.2f}".rstrip("0").rstrip(".")
    return text if text not in ("", "-0") else "0"


def debt_on_hold(debt: Debt) -> bool:
    """A minimum payment above the balance is inconsistent: the payment is held out until confirmed."""
    return debt.minimum_payment > debt.balance


def budgeted_debts(profile: FinancialProfile) -> list:
    """Debts whose minimum payment is budgeted (every debt except those on hold)."""
    return [d for d in profile.debts if not debt_on_hold(d)]


def expense_kind(expense: Expense) -> str:
    return expense.kind or CATEGORY_KIND[expense.category]


def expense_is_fixed(expense: Expense) -> bool:
    return expense.fixed if expense.fixed is not None else expense.category in FIXED_CATEGORIES


def planning_income(profile: FinancialProfile) -> float:
    """Income to budget with. Irregular income uses its lowest recent month."""
    total = 0.0
    for income in profile.incomes:
        if not income.regular and income.minimum_monthly_amount is not None:
            total += income.minimum_monthly_amount
        else:
            total += income.monthly_amount
    return money(total)


def summarize(profile: FinancialProfile) -> FinancialSummary:
    """Calculate every fact the AI and the checks rely on."""
    by_kind = {"essential": 0.0, "obligation": 0.0, "non_essential": 0.0}
    fixed = variable = 0.0
    for e in profile.expenses:
        by_kind[expense_kind(e)] += e.monthly_amount
        if expense_is_fixed(e):
            fixed += e.monthly_amount
        else:
            variable += e.monthly_amount

    total_income = money(sum(i.monthly_amount for i in profile.incomes))
    plan_income = planning_income(profile)
    total_expenses = money(sum(e.monthly_amount for e in profile.expenses))
    min_debt = money(sum(d.minimum_payment for d in budgeted_debts(profile)))   # debts on hold are left out
    required = money(by_kind["essential"] + by_kind["obligation"] + min_debt)
    after_required = money(plan_income - required)
    if not profile.expenses:
        situation = "incomplete"
    elif after_required < 0:
        situation = "deficit"
    elif after_required < TIGHT_SHARE * plan_income:
        situation = "tight"
    else:
        situation = "stable"

    return FinancialSummary(
        total_income=total_income,
        planning_income=plan_income,
        has_irregular_income=any(not i.regular for i in profile.incomes),
        total_expenses=total_expenses,
        essential_expenses=money(by_kind["essential"]),
        obligation_expenses=money(by_kind["obligation"]),
        non_essential_expenses=money(by_kind["non_essential"]),
        fixed_expenses=money(fixed),
        variable_expenses=money(variable),
        minimum_debt_payments=min_debt,
        total_debt=money(sum(d.balance for d in profile.debts)),
        required_spending=required,
        balance_after_required=after_required,
        balance_after_all_spending=money(plan_income - total_expenses - min_debt),
        is_deficit=after_required < 0,
        shortfall=money(max(0.0, -after_required)),
        emergency_fund_months=(money(profile.emergency_fund_balance / required) if required > 0 else None),
        situation=situation,
    )
