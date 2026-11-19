from datetime import date
from decimal import Decimal

from budgeting_engine.spending import summarize_spending
from model.schemas import SpendingRecords


def transaction(posted_on, amount, name="Electricity", category="utilities"):
    return {"posted_on": posted_on, "name": name, "category": category, "amount": amount}


def test_totals_group_categories_and_named_expenses_in_centavos():
    records = SpendingRecords.model_validate({
        "as_of": "2026-11-19",
        "coverage_start": "2026-10-01",
        "transactions": [
            transaction("2026-10-19", "2.67"),
            transaction("2026-10-20", "99.00"),
            transaction("2026-11-03", "2.675"),
            transaction("2026-11-19", "0.34", "ELECTRICITY"),
            transaction("2026-11-07", "3.41", "Internet"),
            transaction("2026-11-18", "7.10", "Groceries", "food"),
        ],
        "category_limits": {"food": "5.20"},
    })

    result = summarize_spending(records)

    assert result.current.total == Decimal("13.53")
    assert result.current.by_category == {"utilities": Decimal("6.43"), "food": Decimal("7.10")}
    assert result.current.by_name["electricity"] == Decimal("3.02")
    assert result.current.names["electricity"] == "Electricity"
    assert result.previous.total == Decimal("2.67")
    assert result.previous.by_name["electricity"] == Decimal("2.67")
    assert result.previous_full.total == Decimal("101.67")
    assert result.previous_full.end == date(2026, 10, 31)
    assert result.category_limits["food"] == Decimal("5.20")


def test_short_previous_month_uses_its_last_day():
    records = SpendingRecords.model_validate({
        "as_of": "2024-03-31",
        "coverage_start": "2024-02-01",
        "transactions": [transaction("2024-02-29", "1.00")],
    })

    result = summarize_spending(records)

    assert result.current.start == date(2024, 3, 1)
    assert result.current.end == date(2024, 3, 31)
    assert result.previous.start == date(2024, 2, 1)
    assert result.previous.end == date(2024, 2, 29)
    assert result.previous.total == Decimal("1.00")


def test_empty_covered_period_is_zero_but_missing_coverage_is_unavailable():
    records = SpendingRecords.model_validate({
        "as_of": "2026-01-19",
        "coverage_start": "2026-01-01",
        "transactions": [],
    })

    result = summarize_spending(records)

    assert result.current.total == Decimal("0.00")
    assert result.current.by_category == {}
    assert result.previous is None
    assert result.previous_full is None
    partial = records.model_copy(update={"coverage_start": date(2026, 1, 2)})
    assert summarize_spending(partial).current is None
