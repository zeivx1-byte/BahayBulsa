"""Plain-language facts and action steps, written by Python.

key_facts()     - a few clear sentences for the AI, instead of a block of
                  similar-looking numbers that a small model can misread.
action_steps()  - concrete things to do this month, taken straight from the
                  final budget, so the amounts are always right.
"""
from budgeting_engine.allocation import HIGH_INTEREST_PERCENT
from budgeting_engine.calculations import budgeted_debts, debt_on_hold, money, plain
from model.schemas import FinancialProfile, FinancialSummary


def key_facts(profile: FinancialProfile, facts: FinancialSummary) -> list:
    """Short, exact sentences describing the household (numbers without commas, for the AI)."""
    out = [_income_fact(profile, facts)]
    if facts.situation == "incomplete":
        if facts.minimum_debt_payments > facts.planning_income:
            out.append(f"No expenses are listed yet, so this is a partial plan: all of the income ({plain(facts.planning_income)}) "
                       f"goes to the known minimum debt payments ({plain(facts.minimum_debt_payments)}), and it covers "
                       "only part of them.")
        elif facts.minimum_debt_payments > 0:
            rest = money(facts.planning_income - facts.minimum_debt_payments)
            out.append(f"No expenses are listed yet, so this is a partial plan: only the known minimum debt payments "
                       f"({plain(facts.minimum_debt_payments)}) are budgeted"
                       + (f", and the remaining {plain(rest)} of income is not allocated until expenses are added."
                          if rest > 0 else "."))
        else:
            out.append("No expenses are listed yet, so no budget can be made.")
        out.extend(_held_debt_facts(profile))
        return out
    out.append(f"Required spending (essentials, obligations and minimum debt payments): {plain(facts.required_spending)}.")
    if facts.is_deficit:
        out.append(f"Income is {plain(facts.shortfall)} short of required spending.")
    else:
        out.append(f"Money left after required spending: {plain(facts.balance_after_required)}.")
    if facts.non_essential_expenses > 0:
        out.append(f"Non-essential spending before this budget: {plain(facts.non_essential_expenses)}.")
    if profile.emergency_fund_balance > 0 and facts.emergency_fund_months is None:
        out.append(f"Emergency fund: {plain(profile.emergency_fund_balance)}. Coverage in months cannot be calculated "
                   "because no required spending is listed.")
    elif profile.emergency_fund_balance > 0:
        out.append(f"Emergency fund: {plain(profile.emergency_fund_balance)}, which covers {facts.emergency_fund_months:.2f} "
                   "months of required spending (goal: 1 month first, then 3 to 6 months).")
    else:
        out.append("There is no emergency fund yet.")
    for d in budgeted_debts(profile):
        rate = d.annual_interest_rate_percent
        if rate is None:
            rate_text = "interest rate unknown"
        else:
            rate_text = f"{rate:g}% a year" + (" (high interest)" if rate > HIGH_INTEREST_PERCENT else "")
        out.append(f"Debt: {d.name}, balance {plain(d.balance)}, minimum payment {plain(d.minimum_payment)}, {rate_text}.")
    out.extend(_held_debt_facts(profile))
    for g in profile.savings_goals:
        left = money(g.target_amount - g.current_amount)
        out.append(f"Savings goal: {g.name}, {plain(left)} still needed" + (f" within {g.months_left} months." if g.months_left else "."))
    return out


def _income_fact(profile: FinancialProfile, facts: FinancialSummary) -> str:
    """The first fact: which income the budget uses, and why."""
    if not facts.has_irregular_income:
        return f"Monthly income: {plain(facts.planning_income)}."
    irregular = [i for i in profile.incomes if not i.regular]
    low = [i.name for i in irregular if i.minimum_monthly_amount is not None]
    stated = [i.name for i in irregular if i.minimum_monthly_amount is None]
    text = f"Income used for this budget: {plain(facts.planning_income)} a month. "
    if not stated:
        return text + (f"Some income is irregular, so the budget uses a low month to be safe "
                       f"(in a usual month the total is {plain(facts.total_income)}).")
    names = ", ".join(stated)
    if not low:
        return text + (f"Some income is irregular ({names}), but its lowest recent month is not given, so planning "
                       "used the stated monthly amount. A low month could be smaller than this.")
    return text + (f"Some income is irregular: the budget uses a low month for {', '.join(low)}, and the stated "
                   f"monthly amount for {names} because its lowest recent month is not given "
                   f"(in a usual month the total is {plain(facts.total_income)}).")


def _held_debt_facts(profile: FinancialProfile) -> list:
    return [f"Debt on hold: {d.name} reports a minimum payment of {plain(d.minimum_payment)} but a balance of only "
            f"{plain(d.balance)}. This does not add up, so the payment is not in the budget until the user confirms "
            "both amounts." for d in profile.debts if debt_on_hold(d)]


def action_steps(profile: FinancialProfile, facts: FinancialSummary, lines: list, currency: str) -> list:
    """Concrete steps for this month, with amounts taken from the final budget."""
    def amount(value):
        return f"{currency} {value:,.2f}"

    held = [f"Confirm the balance and minimum payment of your {d.name} with the lender: a minimum of "
            f"{amount(d.minimum_payment)} is more than the balance of {amount(d.balance)}, so it is not in this budget yet."
            for d in profile.debts if debt_on_hold(d)]

    if facts.situation == "incomplete":
        steps = []
        for line in lines:          # a partial plan: only the known minimum debt payments
            debt = line.name.replace(" (minimum payment)", "")
            if line.amount + 0.005 >= line.needed_amount:
                steps.append(f"Pay the minimum on your {debt} ({amount(line.amount)}) as usual.")
            elif line.amount > 0:
                steps.append(f"Pay {amount(line.amount)} toward your {debt} (the minimum is {amount(line.needed_amount)}) "
                             "and ask the lender for a lower monthly payment.")
        return steps + held + ["List your monthly expenses (rent, food, bills, transportation and others) "
                               "so BudgetAI can build your budget."]

    if facts.is_deficit:
        steps = ["Pay your essentials first (housing, food, utilities and transportation), using the amounts in this budget."]
        for d in budgeted_debts(profile):
            if d.minimum_payment > 0:
                steps.append(f"Before the next due date, contact the lender of '{d.name}' and ask for a lower monthly "
                             "payment or a longer term.")
        if any(e.category == "family_support" for e in profile.expenses):
            steps.append("Talk with your family about a smaller padala until your income covers your essentials.")
        steps.append(f"Look for ways to close the {amount(facts.shortfall)} gap: lower food and utility costs, or find extra income.")
        return steps + held

    steps = list(held)
    for line in sorted(lines, key=lambda l: l.priority):
        if line.source != "rule" or line.amount <= 0:
            continue
        if line.category == "emergency_fund":
            steps.append(f"On payday, move {amount(line.amount)} to a separate savings account for your emergency fund.")
        elif line.category == "debt_payment":
            debt = line.name.replace("Extra payment: ", "")
            steps.append(f"Pay {amount(line.amount)} more than the minimum on your {debt}.")
        elif line.name.startswith("Savings goal: "):
            steps.append(f"Set aside {amount(line.amount)} for your goal '{line.name.replace('Savings goal: ', '')}'.")
        elif line.name == "Extra savings":
            steps.append(f"Save the remaining {amount(line.amount)} for future goals.")
    wants = [l for l in lines if l.priority == 6]
    now = money(sum(l.needed_amount for l in wants))
    planned = money(sum(l.amount for l in wants))
    if wants and planned + 0.005 < now:
        steps.append(f"Keep non-essential spending to {amount(planned)} this month (down from {amount(now)}).")
    return steps
