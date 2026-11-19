"""Tests for budgeting_engine/insights.py: Python's plain-language facts and action steps."""
from budgeting_engine.allocation import build_budget
from budgeting_engine.calculations import summarize
from budgeting_engine.insights import action_steps, key_facts
from model.schemas import FinancialProfile

BASE = {
    "incomes": [{"name": "Salary", "monthly_amount": 30000}],
    "expenses": [
        {"name": "Rent", "category": "housing", "monthly_amount": 10000},
        {"name": "Food", "category": "food", "monthly_amount": 8000},
        {"name": "Dining out", "category": "dining_out", "monthly_amount": 3000},
    ],
    "debts": [{"name": "Credit card", "balance": 20000, "minimum_payment": 1000, "annual_interest_rate_percent": 36}],
}


def run(profile):
    p = FinancialProfile.model_validate(profile)
    f = summarize(p)
    lines, _ = build_budget(p, f)
    return key_facts(p, f), action_steps(p, f, lines, "PHP")


def test_irregular_income_is_explained_without_confusing_numbers():
    facts, _ = run({**BASE, "incomes": [{"name": "Freelance", "monthly_amount": 38000, "regular": False,
                                         "minimum_monthly_amount": 22000}]})
    assert facts[0].startswith("Income used for this budget: 22000 a month. Some income is irregular")
    assert "usual month the total is 38000" in facts[0]


def test_steps_come_straight_from_the_budget():
    # no emergency fund + 36% debt: emergency fund 4000 and credit card 4000 (see test_budgeting_ai.py)
    _, steps = run(BASE)
    assert steps == [
        "On payday, move PHP 4,000.00 to a separate savings account for your emergency fund.",
        "Pay PHP 4,000.00 more than the minimum on your Credit card.",
    ]


def test_lowered_wants_get_a_step():
    _, steps = run({**BASE, "expenses": BASE["expenses"] + [{"name": "Shopping", "category": "shopping", "monthly_amount": 6000}]})
    assert steps[-1] == "Keep non-essential spending to PHP 4,400.00 this month (down from PHP 9,000.00)."


def test_deficit_steps_never_say_stop_paying():
    profile = {**BASE, "incomes": [{"name": "Salary", "monthly_amount": 15000}],
               "expenses": BASE["expenses"] + [{"name": "Padala", "category": "family_support", "monthly_amount": 1500}]}
    facts, steps = run(profile)
    assert "Income is 5500 short of required spending." in facts
    text = " ".join(steps)
    assert "contact the lender of 'Credit card'" in text
    assert "smaller padala" in text
    assert "PHP 5,500.00 gap" in text
    assert "stop" not in text.lower()


def test_incomplete_profile_asks_for_expenses():
    facts, steps = run({"incomes": [{"name": "Salary", "monthly_amount": 25000}]})
    assert facts == ["Monthly income: 25000.", "No expenses are listed yet, so no budget can be made."]
    assert steps[0].startswith("List your monthly expenses")
