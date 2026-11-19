"""Aggregate the caller's recorded expenses in whole centavos."""

from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from model.schemas import ExpenseCategory, SpendingRecords


_ZERO = Decimal("0.00")


@dataclass(frozen=True)
class PeriodSpending:
    start: date
    end: date
    total: Decimal
    by_category: dict[ExpenseCategory, Decimal]
    by_name: dict[str, Decimal]
    names: dict[str, str]
    by_category_name: dict[ExpenseCategory, dict[str, Decimal]]


@dataclass(frozen=True)
class SpendingSummary:
    current: PeriodSpending | None
    previous: PeriodSpending | None
    previous_full: PeriodSpending | None
    category_limits: dict[ExpenseCategory, Decimal]


def summarize_spending(records: SpendingRecords) -> SpendingSummary:
    """Compare month-to-date periods using only days covered by the supplied records.

    A period is unavailable unless its entire date range is covered. An empty
    period with complete coverage represents recorded spending of zero.
    """
    start = records.as_of.replace(day=1)
    current = _summarize_period(records, start, records.as_of)
    previous = previous_full = None
    if start > date.min:
        previous_month_end = start - timedelta(days=1)
        previous_start = previous_month_end.replace(day=1)
        previous_end = previous_month_end.replace(day=min(records.as_of.day, previous_month_end.day))
        previous = _summarize_period(records, previous_start, previous_end)
        previous_full = (previous if previous_end == previous_month_end
                         else _summarize_period(records, previous_start, previous_month_end))

    return SpendingSummary(
        current=current,
        previous=previous,
        previous_full=previous_full,
        category_limits={category: Decimal(str(amount)) for category, amount in records.category_limits.items()},
    )


def _summarize_period(records: SpendingRecords, start: date, end: date) -> PeriodSpending | None:
    if records.coverage_start > start:
        return None

    by_category: dict[ExpenseCategory, Decimal] = defaultdict(lambda: _ZERO)
    by_name: dict[str, Decimal] = defaultdict(lambda: _ZERO)
    names: dict[str, str] = {}
    by_category_name: dict[ExpenseCategory, dict[str, Decimal]] = defaultdict(dict)
    for transaction in records.transactions:
        if start <= transaction.posted_on <= end:
            amount = Decimal(str(transaction.amount))
            name_key = transaction.name.casefold()
            by_category[transaction.category] += amount
            by_name[name_key] += amount
            names.setdefault(name_key, transaction.name)
            named = by_category_name[transaction.category]
            named[name_key] = named.get(name_key, _ZERO) + amount

    return PeriodSpending(
        start, end, sum(by_category.values(), _ZERO), dict(by_category), dict(by_name), names, dict(by_category_name),
    )
