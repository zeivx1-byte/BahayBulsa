"""Write chat replies using only verified spending aggregates and profile data."""

from calendar import month_abbr
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import get_args

from budgeting_engine.calculations import CATEGORY_KIND, expense_kind
from budgeting_engine.spending import PeriodSpending, SpendingSummary
from budgeting_engine.validation import adjustable_expenses, expense_review_tip
from model.schemas import ChatFocus, ExpenseCategory, FinancialProfile, SpendingRecords


CATEGORIES = get_args(ExpenseCategory)
SPENDING_TOPICS = frozenset({
    "spending", "largest_category", "category_limit", "spending_comparison", "expense_trend",
    "reduce_expenses", "unusual_spending", "transaction", "monthly_budget", "income",
    "upcoming_bills", "recurring_bills",
})
_ZERO = Decimal("0.00")


def category_label(category: str) -> str:
    return category.replace("_", " ")


def _amount(currency: str, amount: Decimal | float) -> str:
    return f"{currency} {amount:,.2f}"


def _dates(period: PeriodSpending) -> str:
    return _month_through(period.end)


def _month_through(day: date) -> str:
    return f"{month_abbr[day.month]} 1–{day.day}, {day.year}"


def _movement(before: Decimal, after: Decimal, currency: str) -> str:
    if after == before:
        return "the same amount"
    direction = "more" if after > before else "less"
    return f"{_amount(currency, abs(after - before))} {direction}"


def explain_spending(
    focus: ChatFocus,
    target: tuple[str, str] | None,
    summary: SpendingSummary | None,
    profile: FinancialProfile | None,
    currency: str,
    records: SpendingRecords | None,
) -> str:
    topic = focus.topic
    if topic == "income":
        return _income(focus.period, records, currency)
    if topic in {"upcoming_bills", "recurring_bills"}:
        return _bills(topic, target, records, currency)
    if topic == "reduce_expenses":
        return _reduce_expenses(summary, profile, currency)
    if summary is None:
        return "Send dated expense records to see actual spending. The monthly amounts in your profile are for planning."
    if topic in {"spending_comparison", "expense_trend", "unusual_spending"}:
        return _compare(focus, target, summary, currency)

    period = summary.previous_full if focus.period == "previous" else summary.current
    if period is None:
        return "Send records covering the requested month from its first day so I can calculate spending for that period."
    dates = _dates(period)
    match topic:
        case "largest_category":
            if not period.by_category:
                return f"There were no recorded expenses for {dates}."
            largest = max(period.by_category.values())
            names = [category_label(name) for name, total in sorted(period.by_category.items()) if total == largest]
            if len(names) == 1:
                share = (largest * 100 / period.total).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)
                return (f"{names[0].capitalize()} was your largest recorded expense category for {dates}, "
                        f"at {_amount(currency, largest)} ({share}% of recorded spending).")
            return f"{', '.join(names).capitalize()} tied for your highest spending for {dates}, at {_amount(currency, largest)} each."
        case "monthly_budget":
            return _monthly_budget(period, summary, records, currency, focus.period)
        case "category_limit":
            if target is None or target[0] != "category":
                return "Which category limit should I check? For example, ask about your food budget."
            return _check_limit(target[1], period, summary, records, currency, focus.period)
        case "transaction" | "spending" if target is not None and target[0] == "expense":
            name = target[1]
            spent = period.by_name.get(name.casefold())
            if spent is None:
                return f"There are no recorded payments for {name} for {dates}."
            return f"Recorded payments for {name} came to {_amount(currency, spent)} for {dates}."
        case "transaction":
            return "Which recorded expense should I look up? Use the name on the expense."
        case _:
            if target is not None and target[0] == "category":
                name = category_label(target[1])
                total = period.by_category.get(target[1])
                if total is None:
                    return f"There are no recorded {name} transactions for {dates}."
                return f"Your recorded {name} spending for {dates} was {_amount(currency, total)}."
            reply = f"You recorded {_amount(currency, period.total)} in expenses for {dates}."
            if focus.detail == "breakdown" and period.by_category:
                largest = sorted(period.by_category.items(), key=lambda item: (-item[1], item[0]))[:3]
                breakdown = ", ".join(f"{category_label(name)} {_amount(currency, value)}" for name, value in largest)
                reply += f" The largest recorded categories were {breakdown}."
            return reply


def _income(period: str, records: SpendingRecords | None, currency: str) -> str:
    if records is not None:
        end = records.as_of
        amount = records.income_total
        if period == "previous" and end.replace(day=1) > date.min:
            end = end.replace(day=1) - timedelta(days=1)
            amount = records.previous_income_total
        if amount is not None:
            return f"You recorded {_amount(currency, amount)} in income for {_month_through(end)}."
    return "I need dated income records for that month to give you a recorded total."


def _bills(
    topic: str, target: tuple[str, str] | None, records: SpendingRecords | None, currency: str,
) -> str:
    if records is None or records.upcoming_bills is None:
        return "Add upcoming bills to your ledger to check their amounts and due dates."
    bills = records.upcoming_bills
    label = "recurring bills" if topic == "recurring_bills" else "bills"
    if topic == "recurring_bills":
        bills = [bill for bill in bills if bill.recurring]
    if target is not None and target[0] == "bill":
        bills = [bill for bill in bills if bill.name.casefold() == target[1].casefold()]
    if not bills:
        return f"There are no matching {label} in your queue."
    ordered = sorted(bills, key=lambda bill: (bill.due_on, bill.name.casefold()))
    lines = [f"{bill.name} is due {month_abbr[bill.due_on.month]} {bill.due_on.day}, {bill.due_on.year} "
             f"for {_amount(currency, bill.amount)}" + (" (repeats monthly)." if bill.recurring else ".")
             for bill in ordered[:3]]
    if len(ordered) > 3:
        lines.append(f"Your queue has {len(ordered) - 3} more matching {label}.")
    return " ".join(lines)


def _monthly_budget(
    period: PeriodSpending, summary: SpendingSummary, records: SpendingRecords | None, currency: str, period_name: str,
) -> str:
    recorded = f"You recorded {_amount(currency, period.total)} in expenses for {_dates(period)}."
    previous = period_name == "previous"
    budget = (records.previous_monthly_budget if previous else records.monthly_budget) if records else None
    if budget is not None:
        return f"{recorded} {_budget_status(period.total, Decimal(str(budget)), currency, 'monthly budget')}"
    limits = _category_limits_for_period(summary, records, period_name)
    if limits:
        monitored = sum((period.by_category.get(name, _ZERO) for name in limits), _ZERO)
        limit = sum(limits.values(), _ZERO)
        return (f"{recorded} Categories with saved limits account for {_amount(currency, monitored)}. "
                f"{_budget_status(monitored, limit, currency, 'combined limits for those categories')} "
                "Set an overall monthly budget to compare all expenses.")
    if previous:
        return f"{recorded} I need the overall budget saved for that month to compare it."
    return f"{recorded} Set a monthly budget to see how much of it you have used."


def _budget_status(spent: Decimal, limit: Decimal, currency: str, label: str) -> str:
    amount = _amount(currency, limit)
    if spent > limit:
        return f"That's {_amount(currency, spent - limit)} over your {amount} {label}."
    if spent == limit:
        return f"You've reached your {amount} {label}."
    return f"You have {_amount(currency, limit - spent)} left of your {amount} {label}."


def _check_limit(
    category: str, period: PeriodSpending, summary: SpendingSummary, records: SpendingRecords | None,
    currency: str, period_name: str,
) -> str:
    label = category_label(category)
    spent = period.by_category.get(category, _ZERO)
    recorded = (f"You recorded {_amount(currency, spent)} in {label} spending for {_dates(period)}."
                if category in period.by_category else f"There are no recorded {label} transactions for {_dates(period)}.")
    limit = _category_limits_for_period(summary, records, period_name).get(category)
    if limit is None:
        if period_name == "previous":
            return f"{recorded} To check last month's budget, provide the limit that applied that month."
        return f"{recorded} Add a monthly {label} limit to check how much of it you used."
    return f"{recorded} {_budget_status(spent, limit, currency, 'monthly limit')}"


def _category_limits_for_period(
    summary: SpendingSummary, records: SpendingRecords | None, period_name: str,
) -> dict[ExpenseCategory, Decimal]:
    if period_name != "previous":
        return summary.category_limits
    return ({name: Decimal(str(amount)) for name, amount in records.previous_category_limits.items()}
            if records else {})


def _compare(
    focus: ChatFocus, target: tuple[str, str] | None, summary: SpendingSummary, currency: str,
) -> str:
    topic = focus.topic
    current = summary.current
    previous = summary.previous_full if focus.period == "previous" else summary.previous
    if current is None or previous is None:
        return "Send expense records from the first day of last month through the date of this report to compare spending."
    dates = f"{_dates(current)} versus {_dates(previous)}"
    if topic == "unusual_spending":
        differences = {name: total - previous.by_category.get(name, _ZERO)
                       for name, total in current.by_category.items()}
        name = max(sorted(differences), key=differences.get, default=None)
        if name is None or differences[name] <= 0:
            return f"Recorded spending rose in none of your categories for {dates}."
        return (f"{category_label(name).capitalize()} had the largest increase for {dates}, "
                f"up {_amount(currency, differences[name])}. Review the recorded purchases in that category to see what changed.")
    if topic == "expense_trend":
        if target is None or target[0] not in {"category", "expense"}:
            return "Which recurring expense or category would you like to compare? Use the name on the expense."
        kind, name = target
        change = ""
        if kind == "category":
            before = previous.by_category.get(name, _ZERO)
            after = current.by_category.get(name, _ZERO)
            if name not in previous.by_category and name not in current.by_category:
                return f"There are no recorded {category_label(name)} transactions for {dates}."
            change = _category_difference(name, current, previous, currency)
            name = category_label(name).capitalize()
        else:
            before = previous.by_name.get(name.casefold(), _ZERO)
            after = current.by_name.get(name.casefold(), _ZERO)
            if name.casefold() not in previous.by_name and name.casefold() not in current.by_name:
                return f"There are no recorded payments for {name} for {dates}."
        reply = (f"{name} came to {_amount(currency, after)} for {_dates(current)} and "
                 f"{_amount(currency, before)} for {_dates(previous)}. That's {_movement(before, after, currency)}.")
        if change:
            reply += f" {change}"
        elif after != before:
            reply += " Check the underlying bills or purchases for changes in timing, usage, rates, or fees."
        if focus.period == "previous" and current.end.day != previous.end.day:
            reply += " This compares this month so far with the full prior month."
        return reply
    reply = (f"You recorded {_amount(currency, current.total)} in expenses for {_dates(current)}, "
             f"compared with {_amount(currency, previous.total)} for {_dates(previous)}. "
             f"That's {_movement(previous.total, current.total, currency)}.")
    if focus.period == "previous" and current.end.day != previous.end.day:
        reply += " This compares this month so far with the full prior month."
    return reply


def _category_difference(category: str, current: PeriodSpending, previous: PeriodSpending, currency: str) -> str:
    current_items = current.by_category_name.get(category, {})
    previous_items = previous.by_category_name.get(category, {})
    changed = {name: current_items.get(name, _ZERO) - previous_items.get(name, _ZERO)
               for name in current_items.keys() | previous_items.keys()}
    if not changed:
        return ""
    name, difference = max(sorted(changed.items()), key=lambda item: abs(item[1]))
    if difference == 0:
        return ""
    label = current.names.get(name) or previous.names[name]
    direction = "increased" if difference > 0 else "decreased"
    return f"The biggest recorded item change was {label}, which {direction} by {_amount(currency, abs(difference))}."


def _reduce_expenses(summary: SpendingSummary | None, profile: FinancialProfile | None, currency: str) -> str:
    if summary is not None and summary.current is not None:
        totals = summary.current.by_category
        overages = {name: totals.get(name, _ZERO) - limit for name, limit in summary.category_limits.items()}
        over = max(sorted(overages), key=overages.get, default=None)
        if over is not None and overages[over] > 0:
            name = category_label(over)
            return (f"Start with {name}. You are {_amount(currency, overages[over])} over its monthly limit "
                    "in the supplied records. Review those purchases and identify which ones you can defer or replace.")
        optional = [name for name in totals if CATEGORY_KIND[name] == "non_essential"]
        if optional:
            name = max(sorted(optional), key=totals.get)
            return (f"Review {category_label(name)} first. You recorded {_amount(currency, totals[name])} there "
                    f"for {_dates(summary.current)}. Look for purchases or renewals you could skip next month.")
        if totals:
            name = max(sorted(totals), key=totals.get)
            return (f"{category_label(name).capitalize()} was your largest recorded cost at "
                    f"{_amount(currency, totals[name])} for {_dates(summary.current)}. "
                    "Review the entries in that category for charges you can compare, defer, or replace.")

    if profile is None:
        return "Add dated expenses so I can point to specific costs to review."
    discretionary = [expense for expense in profile.expenses
                     if expense.monthly_amount > 0 and expense_kind(expense) == "non_essential"]
    if discretionary:
        item = max(discretionary, key=lambda expense: expense.monthly_amount)
        return (f"Your largest optional expense in the profile is {item.name} at "
                f"{_amount(currency, item.monthly_amount)} a month. Check which part of that cost you could pause or reduce.")
    item = max(adjustable_expenses(profile), key=lambda expense: expense.monthly_amount, default=None)
    if item is not None:
        return (f"Review {item.name}, listed at {_amount(currency, item.monthly_amount)} a month. "
                f"{expense_review_tip(item)}")
    return "Add your monthly expenses or dated transactions so I can point to specific costs to review."
