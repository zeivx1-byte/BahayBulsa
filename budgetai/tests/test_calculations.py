"""Tests for the Python facts in budgeting_engine/calculations.py.

Every expected number here was worked out by hand, so the tests prove the
calculations are exactly right.
"""
from budgeting_engine.calculations import plain, planning_income, summarize
from model.schemas import FinancialProfile


def make(**overrides):
    base = {
        "incomes": [{"name": "Salary", "monthly_amount": 30000}],
        "expenses": [
            {"name": "Rent", "category": "housing", "monthly_amount": 8000},           # essential, fixed
            {"name": "Food", "category": "food", "monthly_amount": 7000},              # essential, variable
            {"name": "Tuition", "category": "education", "monthly_amount": 2000},      # obligation, fixed
            {"name": "Dining out", "category": "dining_out", "monthly_amount": 1500},  # non-essential, variable
            {"name": "Netflix", "category": "subscriptions", "monthly_amount": 500},   # non-essential, fixed
        ],
        "debts": [{"name": "Credit card", "balance": 20000, "minimum_payment": 1000}],
        "emergency_fund_balance": 9000,
    }
    base.update(overrides)
    return FinancialProfile.model_validate(base)


def test_totals_and_kinds():
    f = summarize(make())
    assert f.total_income == 30000
    assert f.planning_income == 30000
    assert f.total_expenses == 19000
    assert f.essential_expenses == 15000
    assert f.obligation_expenses == 2000
    assert f.non_essential_expenses == 2000
    assert f.fixed_expenses == 10500        # rent + tuition + netflix
    assert f.variable_expenses == 8500      # food + dining out
    assert f.minimum_debt_payments == 1000
    assert f.total_debt == 20000


def test_required_spending_and_balances():
    f = summarize(make())
    assert f.required_spending == 18000           # 15000 + 2000 + 1000
    assert f.balance_after_required == 12000      # 30000 - 18000
    assert f.balance_after_all_spending == 10000  # 30000 - 19000 - 1000
    assert f.is_deficit is False
    assert f.shortfall == 0
    assert f.emergency_fund_months == 0.5         # 9000 / 18000


def test_deficit_is_detected_with_exact_shortfall():
    f = summarize(make(incomes=[{"name": "Salary", "monthly_amount": 15000}]))
    assert f.is_deficit is True
    assert f.balance_after_required == -3000
    assert f.shortfall == 3000


def test_irregular_income_uses_lowest_month():
    profile = make(incomes=[
        {"name": "Salary", "monthly_amount": 20000},
        {"name": "Freelance", "monthly_amount": 15000, "regular": False, "minimum_monthly_amount": 5000},
    ])
    assert planning_income(profile) == 25000
    f = summarize(profile)
    assert f.total_income == 35000
    assert f.has_irregular_income is True


def test_irregular_income_without_minimum_uses_stated_amount():
    profile = make(incomes=[{"name": "Freelance", "monthly_amount": 15000, "regular": False}])
    assert planning_income(profile) == 15000


def test_user_can_override_kind_and_fixed():
    profile = make(expenses=[
        {"name": "Work software", "category": "subscriptions", "monthly_amount": 1200,
         "kind": "essential", "fixed": True},
    ])
    f = summarize(profile)
    assert f.essential_expenses == 1200
    assert f.non_essential_expenses == 0


def test_no_expenses_or_debts():
    f = summarize(make(expenses=[], debts=[], emergency_fund_balance=0))
    assert f.required_spending == 0
    assert f.emergency_fund_months is None   # cannot divide by zero
    assert f.is_deficit is False


def test_amounts_are_rounded_to_centavos():
    profile = make(expenses=[{"name": "Load", "category": "utilities", "monthly_amount": 0.1},
                             {"name": "Load 2", "category": "utilities", "monthly_amount": 0.2}], debts=[])
    assert summarize(profile).essential_expenses == 0.3   # not 0.30000000000000004


def test_situation_label():
    assert summarize(make()).situation == "stable"                                              # 12000 of 30000 left
    assert summarize(make(incomes=[{"name": "S", "monthly_amount": 19000}])).situation == "tight"   # 1000 < 10%
    assert summarize(make(incomes=[{"name": "S", "monthly_amount": 15000}])).situation == "deficit"


def test_situation_is_incomplete_without_expenses():
    assert summarize(make(expenses=[], debts=[])).situation == "incomplete"


def test_plain_numbers_have_no_commas():
    assert plain(4000.0) == "4000"
    assert plain(10401) == "10401"
    assert plain(1699.5) == "1699.5"
    assert plain(1234.56) == "1234.56"
    assert plain(0) == "0"
