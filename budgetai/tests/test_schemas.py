"""Tests for the data formats in model/schemas.py: bad input must be rejected."""
import pytest
from pydantic import ValidationError

from model.schemas import AIBudgetAdvice, FinancialProfile

VALID = {
    "incomes": [{"name": "Salary", "monthly_amount": 25000}],
    "expenses": [{"name": "Rent", "category": "housing", "monthly_amount": 7000}],
}


def with_changes(**changes):
    data = {**VALID, **changes}
    return data


def test_valid_profile_is_accepted():
    profile = FinancialProfile.model_validate(VALID)
    assert profile.incomes[0].regular is True        # default
    assert profile.emergency_fund_balance == 0       # default
    assert profile.expenses[0].kind is None          # decided later from the category


@pytest.mark.parametrize("bad, why", [
    (with_changes(incomes=[]), "at least one income is required"),
    (with_changes(incomes=[{"name": "Salary", "monthly_amount": 0}]), "income must be more than 0"),
    (with_changes(expenses=[{"name": "Rent", "category": "housing", "monthly_amount": -5}]), "no negative money"),
    (with_changes(expenses=[{"name": "Rent", "category": "rent", "monthly_amount": 5}]), "unknown category"),
    (with_changes(expenses=[{"name": "", "category": "housing", "monthly_amount": 5}]), "name cannot be empty"),
    (with_changes(debts=[{"name": "Loan", "balance": 1000}]), "debt needs a minimum payment"),
    (with_changes(incomes=[{"name": "Freelance", "monthly_amount": 10000, "regular": False,
                            "minimum_monthly_amount": 20000}]), "minimum cannot exceed average"),
    (with_changes(household_size=0), "household size at least 1"),
    (with_changes(salary=30000), "unknown fields are rejected"),
    (with_changes(incomes=[{"name": "Salary", "monthly_amount": "a lot"}]), "amount must be a number"),
])
def test_invalid_profiles_are_rejected(bad, why):
    with pytest.raises(ValidationError):
        FinancialProfile.model_validate(bad)


def test_ai_advice_rejects_negative_amounts():
    reply = {
        "summary": "", "savings_advice": "", "warnings": [], "missing_information": [],
        "spending_flags": [{"expense_name": "Food", "current_amount": 8000, "suggested_amount": -1, "reason": "x"}],
    }
    with pytest.raises(ValidationError):
        AIBudgetAdvice.model_validate(reply)


def test_ai_advice_schema_lists_every_required_field():
    schema = AIBudgetAdvice.model_json_schema()
    assert set(schema["required"]) == {
        "summary", "savings_advice", "spending_flags", "warnings", "missing_information",
    }
