"""Builds the monthly budget with fixed, explainable budgeting rules.

The budget never allocates more than planning income, and the same household
always gets the same budget. Every line carries the reason for its amount.

THE RULES
1. Required spending first: every essential and obligation expense at its
   current amount, plus every minimum debt payment. A debt whose minimum
   payment is more than its balance is inconsistent: it is left out and the
   user is asked to confirm both amounts.
2. Deficit (income below required spending): fund essentials first, then
   obligations and minimum debt payments, sharing what is left equally by
   percentage. Nothing goes to savings or non-essentials.
3. Otherwise the money left after required spending is split between
   "future" (savings and debt payoff) and non-essential spending. The more
   urgent the situation, the larger the future share:
     high   (emergency fund under 1 month, or high-interest debt)   60%
     medium (emergency fund under 3 months, or a goal with a deadline) 40%
     low    (none of the above)                                      20%
   Non-essentials keep up to their current amount; anything they do not need
   goes to the future. Each non-essential is lowered by the same percentage.
4. The future money is used in this order:
     a. Emergency fund up to 1 month of required spending. If there is also
        high-interest debt, these two share the money half and half.
     b. Extra payments on high-interest debt (above 20% a year), highest first.
     c. Each savings goal's monthly amount (remaining / months left),
        earliest deadline first. Goals without a deadline use 12 months.
     d. Emergency fund up to 3 months.
     e. Extra payments on low-interest debt, highest rate first.
     f. Emergency fund up to 6 months.
     g. The rest of each savings goal.
     h. Anything still left becomes "Extra savings".
5. Partial plan: if no expenses are listed but minimum debt payments are
   known, only those payments are budgeted, every line and a note say it is
   a partial plan, and the rest of the income stays unallocated.

Every amount is a whole number of centavos (see the money policy in
model/schemas.py), so the lines always add up to the total.
"""
import math

from budgeting_engine.calculations import budgeted_debts, expense_kind, money
from budgeting_engine.validation import data_notes
from model.schemas import BudgetLine, FinancialProfile, FinancialSummary

# Change this whenever a rule changes, then regenerate the dataset.
# 1.0  Phase 4 rules.
# 1.1  Phase 5: debts with a minimum above the balance are held out; partial plans
#      are labelled; notes say when irregular income had no low month; all amounts
#      are rounded to centavos half up.
RULES_VERSION = "1.1"
KIND_PRIORITY = {"essential": 1, "obligation": 2}
HIGH_INTEREST_PERCENT = 20.0
FUTURE_SHARE = {"high": 0.60, "medium": 0.40, "low": 0.20}
STARTER_MONTHS, STANDARD_MONTHS, FULL_MONTHS = 1, 3, 6
DEFAULT_GOAL_MONTHS = 12


def floor_money(value: float) -> float:
    """Round down to centavos, so shares never add up to more than the total."""
    return math.floor(value * 100 + 1e-6) / 100


def split_proportionally(needs: list, total: float) -> list:
    """Share `total` among `needs` by the same percentage, exact to the centavo."""
    need_sum = sum(needs)
    if need_sum <= 0 or total <= 0:
        return [0.0 for _ in needs]
    if total >= need_sum:
        return [money(n) for n in needs]
    amounts = [floor_money(n * total / need_sum) for n in needs]
    spare_cents = round((total - sum(amounts)) * 100)
    for i, need in enumerate(needs):
        if spare_cents <= 0:
            break
        if amounts[i] + 0.01 <= need:
            amounts[i] = money(amounts[i] + 0.01)
            spare_cents -= 1
    return amounts


def required_lines(profile: FinancialProfile) -> list:
    """Rule 1: essentials, obligations and minimum debt payments at their current amounts.

    Debts on hold (minimum above the balance) are left out until the user confirms them.
    """
    lines = []
    for e in profile.expenses:
        kind = expense_kind(e)
        if kind in KIND_PRIORITY:
            lines.append(BudgetLine(
                name=e.name, category=e.category, priority=KIND_PRIORITY[kind],
                amount=e.monthly_amount, needed_amount=e.monthly_amount, source="required",
                reason=f"{kind.capitalize()}: kept at the current amount.",
            ))
    for d in budgeted_debts(profile):
        if d.minimum_payment > 0:
            lines.append(BudgetLine(
                name=f"{d.name} (minimum payment)", category="debt_payment", priority=2,
                amount=d.minimum_payment, needed_amount=d.minimum_payment, source="required",
                reason="The minimum payment avoids penalties and protects your credit.",
            ))
    return lines


def fund_by_priority(lines: list, income: float, notes: list) -> list:
    """Rule 2 (deficit): fund priority 1 first, then priority 2, sharing what is left."""
    remaining = income
    for level in (1, 2):
        group = [line for line in lines if line.priority == level]
        need = money(sum(line.needed_amount for line in group))
        if need == 0:
            continue
        if remaining >= need:
            remaining = money(remaining - need)
            continue
        share = remaining / need
        for line, amount in zip(group, split_proportionally([l.needed_amount for l in group], remaining)):
            line.amount = amount
            line.reason = f"Funded at {share:.0%}: income is not enough for all required spending."
        for line in lines:
            if line.priority > level:
                line.amount = 0.0
                line.reason = "Not funded: income runs out before this line."
        label = "essentials" if level == 1 else "obligations and minimum debt payments"
        notes.append(f"Income covers only {share:.0%} of {label}, so each of those lines is funded at {share:.0%}.")
        if level == 1:
            notes.append("Nothing is left for obligations and minimum debt payments.")
        break
    return lines


def _rate(debt) -> float:
    """Interest rate for sorting; an unknown rate is treated as high to be safe."""
    rate = debt.annual_interest_rate_percent
    return rate if rate is not None else HIGH_INTEREST_PERCENT + 0.01


def urgency(profile: FinancialProfile, facts: FinancialSummary) -> tuple:
    """Return (level, reason) for rule 3."""
    has_ef = facts.required_spending > 0
    # Use the exact ratio, not the rounded figure shown to users: 0.999 months is still under 1 month.
    months = profile.emergency_fund_balance / facts.required_spending if has_ef else 0.0
    high_debt = any(_rate(d) > HIGH_INTEREST_PERCENT and d.balance > d.minimum_payment for d in profile.debts)
    if has_ef and months < STARTER_MONTHS and high_debt:
        return "high", "the emergency fund covers less than 1 month and there is high-interest debt"
    if has_ef and months < STARTER_MONTHS:
        return "high", "the emergency fund covers less than 1 month"
    if high_debt:
        return "high", "there is high-interest debt"
    if has_ef and months < STANDARD_MONTHS:
        return "medium", "the emergency fund covers less than 3 months"
    if any(g.months_left and g.target_amount > g.current_amount for g in profile.savings_goals):
        return "medium", "there is a savings goal with a deadline"
    return "low", "the emergency fund is healthy and there is no high-interest debt"


class _Pots:
    """Collects the future money for each destination, in the order it is filled."""

    def __init__(self, amount: float):
        self.remaining = money(amount)
        self.order, self.amounts, self.info = [], {}, {}

    def give(self, key, cap, name, category, priority, reason) -> float:
        amount = money(min(max(cap, 0.0), self.remaining))
        if amount <= 0:
            return 0.0
        if key not in self.amounts:
            self.order.append(key)
            self.amounts[key] = 0.0
        self.amounts[key] = money(self.amounts[key] + amount)
        self.info[key] = (name, category, priority, reason)   # the latest stage explains the line
        self.remaining = money(self.remaining - amount)
        return amount


def mark_partial(lines: list, income: float, notes: list) -> None:
    """Rule 5: label a plan that budgets only the known debt payments."""
    known = money(sum(line.amount for line in lines))
    needed = money(sum(line.needed_amount for line in lines))
    rest = money(max(0.0, income - known))
    for line in lines:
        line.reason = f"Partial plan (no expenses listed yet): {line.reason}"
    funded = f"{known:,.2f}" if known + 0.005 >= needed else f"{known:,.2f} of {needed:,.2f}"
    notes.append(f"Partial plan: no expenses are listed yet, so only the known minimum debt payments "
                 f"({funded}) are budgeted."
                 + (f" The remaining {rest:,.2f} of income is left unallocated until the monthly expenses are added."
                    if rest > 0 else " The monthly expenses are needed for a full budget."))


def build_budget(profile: FinancialProfile, facts: FinancialSummary) -> tuple:
    """Return (budget lines, rule notes) for one household."""
    notes = data_notes(profile)
    lines = required_lines(profile)
    if facts.is_deficit:
        lines = fund_by_priority(lines, facts.planning_income, notes)
        if not profile.expenses:
            mark_partial(lines, facts.planning_income, notes)
        return lines, notes
    if not profile.expenses:
        if lines:
            mark_partial(lines, facts.planning_income, notes)
        else:
            notes.append("No expenses are listed, so the income is left unallocated until they are added.")
        return lines, notes

    available = facts.balance_after_required
    required = facts.required_spending
    level, why = urgency(profile, facts)

    # Rule 3: split between non-essential spending and the future.
    wants = [e for e in profile.expenses if expense_kind(e) == "non_essential"]
    wants_now = money(sum(e.monthly_amount for e in wants))
    wants_budget = money(min(wants_now, (1 - FUTURE_SHARE[level]) * available))
    notes.append(f"Because {why}, at least {FUTURE_SHARE[level]:.0%} of the {available:,.2f} left after required "
                 "spending goes to savings and debt payoff.")
    if wants_now > 0:
        keep = wants_budget / wants_now
        for e, amount in zip(wants, split_proportionally([e.monthly_amount for e in wants], wants_budget)):
            reason = ("Kept at the current amount." if keep >= 0.9999 else
                      f"Lowered by {1 - keep:.0%} (from {e.monthly_amount:,.2f}) because {why}.")
            lines.append(BudgetLine(name=e.name, category=e.category, priority=6, amount=amount,
                                    needed_amount=e.monthly_amount, source="rule", reason=reason))
        if keep < 0.9999:
            notes.append(f"Non-essential spending is lowered by {1 - keep:.0%}, from {wants_now:,.2f} to {wants_budget:,.2f}.")

    # Rule 4: use the future money in order.
    pots = _Pots(available - wants_budget)
    balance = profile.emergency_fund_balance
    high_debts = sorted([(n, d) for n, d in enumerate(profile.debts, 1)
                         if _rate(d) > HIGH_INTEREST_PERCENT and d.balance > d.minimum_payment], key=lambda x: -_rate(x[1]))
    low_debts = sorted([(n, d) for n, d in enumerate(profile.debts, 1)
                        if _rate(d) <= HIGH_INTEREST_PERCENT and d.balance > d.minimum_payment], key=lambda x: -_rate(x[1]))
    goals = sorted([(n, g) for n, g in enumerate(profile.savings_goals, 1) if g.target_amount > g.current_amount],
                   key=lambda x: x[1].months_left or 10_000)

    def emergency(target_months, cap=None):
        room = max(0.0, target_months * required - balance - pots.amounts.get("EF", 0.0))
        return pots.give("EF", room if cap is None else min(room, cap), "Emergency fund", "emergency_fund", 3,
                         f"Builds the emergency fund toward {target_months} month(s) of required spending "
                         f"(it covers {facts.emergency_fund_months or 0:.2f} months now).")

    def pay_debts(debts, priority, label):
        for n, d in debts:
            room = d.balance - d.minimum_payment - pots.amounts.get(f"D{n}", 0.0)
            rate = d.annual_interest_rate_percent
            rate_text = f"{rate:g}%" if rate is not None else "unknown-rate"
            pots.give(f"D{n}", room, f"Extra payment: {d.name}", "debt_payment", priority,
                      f"Extra payment on {label} {rate_text} debt; the highest rate is paid first to save interest.")

    # a + b: starter emergency fund and high-interest debt
    if high_debts and required > 0 and balance < STARTER_MONTHS * required:
        emergency(STARTER_MONTHS, cap=pots.remaining / 2)
        pay_debts(high_debts, 4, "high-interest")
        emergency(STARTER_MONTHS)
    else:
        if required > 0:
            emergency(STARTER_MONTHS)
        pay_debts(high_debts, 4, "high-interest")
    # c: monthly amount for each savings goal
    for n, g in goals:
        room = g.target_amount - g.current_amount
        months = g.months_left or DEFAULT_GOAL_MONTHS
        monthly = money(room / months)
        given = pots.give(f"G{n}", monthly, f"Savings goal: {g.name}", "savings_goal", 5,
                          f"{monthly:,.2f} a month reaches '{g.name}' ({room:,.2f} still needed) in {months} months.")
        if g.months_left and given + 0.005 < monthly:
            notes.append(f"'{g.name}' needs {monthly:,.2f} a month to be reached on time, but only {given:,.2f} fits "
                         "this month. Consider a later deadline, a smaller target, or spending less on wants.")
    # d, e, f: emergency fund to 3 months, low-interest debt, emergency fund to 6 months
    if required > 0:
        emergency(STANDARD_MONTHS)
    pay_debts(low_debts, 5, "low-interest")
    if required > 0:
        emergency(FULL_MONTHS)
    # g: the rest of each savings goal
    for n, g in goals:
        room = g.target_amount - g.current_amount - pots.amounts.get(f"G{n}", 0.0)
        pots.give(f"G{n}", room, f"Savings goal: {g.name}", "savings_goal", 5,
                  f"Extra money toward '{g.name}' after the other priorities are covered.")
    # h: anything left
    pots.give("XS", pots.remaining, "Extra savings", "savings_goal", 5,
              "Money left after every other priority is covered, saved for the future.")

    for key in pots.order:
        name, category, priority, reason = pots.info[key]
        amount = pots.amounts[key]
        lines.append(BudgetLine(name=name, category=category, priority=priority, amount=amount,
                                needed_amount=amount, source="rule", reason=reason))
    return lines, notes
