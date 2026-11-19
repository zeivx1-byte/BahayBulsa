"""Data checks and AI advice clean-up.

- find_missing_data(): information that is missing or inconsistent, phrased for the user.
- data_notes(): notes that explain how Python handled that information in the budget.
- clean_ai_advice(): makes the AI's advice agree with the facts and the final budget.
"""
import re
from collections import Counter

from budgeting_engine.calculations import debt_on_hold, expense_is_fixed, expense_kind, money, planning_income
from model.schemas import AIBudgetAdvice, Expense, FinancialProfile, SpendingFlag

_EXPENSE_REVIEW_TIPS = {
    "food": "Compare prices before buying.",
    "utilities": "Check your bills for avoidable charges and usage.",
    "transportation": "Check whether a lower-fare route fits your commute.",
}
_QUANTITY = re.compile(
    r"[%₱$€£¥]|"
    r"\b(?:half|quarter|one[- ]third|two[- ]thirds|twice|double|triple|"
    r"percent|per cent|pesos?|centavos?|dollars?|cents?|PHP)\b|"
    r"\b(?:zero|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|"
    r"thirteen|fourteen|fifteen|sixteen|seventeen|eighteen|nineteen|twenty|thirty|forty|"
    r"fifty|sixty|seventy|eighty|ninety|a few|several)"
    r"[\s-]+(?:hundred|thousand|million|billion|days?|weeks?|months?|years?)\b",
    re.IGNORECASE,
)


def find_missing_data(profile: FinancialProfile) -> list:
    """List information that is missing or inconsistent and would improve the budget."""
    missing = []
    if not profile.expenses:
        missing.append("No expenses are listed, so a full budget cannot be made yet.")
    if profile.household_size is None:
        missing.append("The household size is not given.")
    for income in profile.incomes:
        if not income.regular and income.minimum_monthly_amount is None:
            missing.append(f"'{income.name}' is irregular, but its lowest recent monthly amount is not given, "
                           f"so planning used the stated monthly amount ({income.monthly_amount:,.2f}).")
    for debt in profile.debts:
        if debt_on_hold(debt):
            missing.append(f"'{debt.name}' has a minimum payment of {debt.minimum_payment:,.2f} but a balance of only "
                           f"{debt.balance:,.2f}. Please confirm both amounts; this payment is not in the budget until then.")
        if debt.annual_interest_rate_percent is None:
            missing.append(f"The interest rate of '{debt.name}' is not given.")
    for goal in profile.savings_goals:
        if goal.months_left is None:
            missing.append(f"The deadline of the savings goal '{goal.name}' is not given.")
    return missing


def data_notes(profile: FinancialProfile) -> list:
    """How Python handled inconsistent or incomplete information (shown with the budget)."""
    notes = []
    for income in profile.incomes:
        if not income.regular and income.minimum_monthly_amount is None:
            notes.append(f"'{income.name}' is irregular and its lowest recent month is not given, so planning used the "
                         f"stated monthly amount ({income.monthly_amount:,.2f}). In a low month this budget may be too high.")
    for debt in profile.debts:
        if debt_on_hold(debt):
            notes.append(f"'{debt.name}' is not budgeted: its minimum payment ({debt.minimum_payment:,.2f}) is more than "
                         f"its balance ({debt.balance:,.2f}). Please confirm both amounts.")
    return notes


def adjustable_expenses(profile: FinancialProfile) -> list[Expense]:
    """List unambiguous variable costs the AI may suggest reviewing."""
    name_counts = Counter(expense.name.casefold() for expense in profile.expenses)
    return [expense for expense in profile.expenses
            if expense.category in _EXPENSE_REVIEW_TIPS
            and expense_kind(expense) != "non_essential"
            and not expense_is_fixed(expense)
            and expense.monthly_amount >= 0.02
            and name_counts[expense.name.casefold()] == 1]


def expense_review_tip(expense: Expense) -> str:
    """Give a practical next step for an adjustable expense."""
    return _EXPENSE_REVIEW_TIPS[expense.category]


def mentions_quantity(text: str) -> bool:
    """Find common numeric and spelled-out quantities in model prose."""
    return any(character.isnumeric() for character in text) or bool(_QUANTITY.search(text))


def _covers(missing_item: str, question: str) -> bool:
    """Does an AI question ask about this missing item?"""
    q = question.lower()
    if "'" in missing_item:                                  # items about a named income, debt or goal
        return missing_item.split("'")[1].lower() in q
    if missing_item.startswith("No expenses"):
        return "expense" in q or "spend" in q
    if "household size" in missing_item:
        return any(word in q for word in ("household", "people", "live", "family"))
    return False


def partial_plan_summary(profile: FinancialProfile, lines: list, currency: str) -> str:
    known = money(sum(line.amount for line in lines))
    needed = money(sum(line.needed_amount for line in lines))
    rest = money(max(0.0, planning_income(profile) - known))
    if known + 0.005 < needed:
        return (f"This is a partial plan: no expenses are listed yet, and all of the income ({currency} {known:,.2f}) "
                f"goes to the known minimum debt payments ({currency} {needed:,.2f}), covering only part of them. "
                "Please add your monthly expenses and talk with your lenders about lower payments.")
    text = (f"This is a partial plan: no expenses are listed yet, so only the known minimum debt payments "
            f"({currency} {known:,.2f}) are budgeted.")
    if rest > 0:
        return text + f" The remaining {currency} {rest:,.2f} of income is not allocated until your monthly expenses are added."
    return text + " Please add your monthly expenses so BudgetAI can build your full budget."


def clean_ai_advice(advice: AIBudgetAdvice, profile: FinancialProfile, lines: list, missing: list,
                    currency: str = "PHP") -> tuple:
    """Make the AI's advice agree with the facts and the final budget. Returns (advice, notes).

    - Spending flags use a listed adjustable expense, a lower amount above 0,
      and a practical check for that expense. Flags for non-essential spending use the final budget.
    - Questions: kept only when information is missing, and only if together they ask
      about every missing item; otherwise Python's own list is used.
    - Partial plan: the summary must say so; if it does not, Python's summary replaces it.
    """
    notes = []
    flags, dropped = [], 0
    by_name = {expense.name.casefold(): expense for expense in adjustable_expenses(profile)}
    flagged = set()
    for flag in advice.spending_flags:
        name = flag.expense_name.strip().casefold()
        expense = by_name.get(name)
        if expense is None or name in flagged or not 0 < flag.suggested_amount < expense.monthly_amount:
            dropped += 1
            continue
        suggested = money(flag.suggested_amount)
        if not 0 < suggested < expense.monthly_amount:
            dropped += 1
            continue
        flags.append(flag.model_copy(update={"expense_name": expense.name, "current_amount": expense.monthly_amount,
                                             "suggested_amount": suggested, "reason": expense_review_tip(expense)}))
        flagged.add(name)
    for line in lines:
        if line.priority == 6 and line.amount + 0.005 < line.needed_amount:
            flags.append(SpendingFlag(expense_name=line.name, current_amount=line.needed_amount,
                                      suggested_amount=line.amount, reason=line.reason))
    if dropped:
        notes.append(f"Removed {dropped} AI spending flag(s) that did not match the expenses or the budget.")

    questions = advice.missing_information
    if not missing:
        if questions:
            notes.append(f"Removed {len(questions)} AI question(s): no information is missing.")
        questions = []
    else:
        relevant = [q for q in questions if any(_covers(item, q) for item in missing)]
        if all(any(_covers(item, q) for q in relevant) for item in missing):
            questions = relevant
        else:
            if questions:
                notes.append("Used Python's questions: the AI's questions did not cover every missing item.")
            questions = list(missing)

    summary = advice.summary
    if lines and not profile.expenses and "partial plan" not in summary.lower():
        summary = partial_plan_summary(profile, lines, currency)
        notes.append("Replaced the AI summary: it did not say that this is a partial plan.")

    return advice.model_copy(update={"spending_flags": flags, "missing_information": questions,
                                     "summary": summary}), notes
