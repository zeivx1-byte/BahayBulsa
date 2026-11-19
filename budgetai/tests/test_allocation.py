"""Tests for the budgeting rules in budgeting_engine/allocation.py.

Each expected amount is worked out by hand in the comments, so these tests
also document exactly how the rules behave.
"""
import random

from budgeting_engine.allocation import build_budget, required_lines, urgency
from budgeting_engine.calculations import summarize
from model.schemas import FinancialProfile

PROFILE = {
    "incomes": [{"name": "Salary", "monthly_amount": 30000}],
    "expenses": [
        {"name": "Rent", "category": "housing", "monthly_amount": 10000},          # essential
        {"name": "Food", "category": "food", "monthly_amount": 8000},              # essential
        {"name": "Tuition", "category": "education", "monthly_amount": 2000},      # obligation
        {"name": "Dining out", "category": "dining_out", "monthly_amount": 3000},  # non-essential
        {"name": "Netflix", "category": "subscriptions", "monthly_amount": 500},   # non-essential
    ],
    "debts": [{"name": "Credit card", "balance": 20000, "minimum_payment": 1000, "annual_interest_rate_percent": 36}],
    "savings_goals": [{"name": "Laptop", "target_amount": 30000, "current_amount": 6000, "months_left": 8}],
    "emergency_fund_balance": 5000,
}
# required = 10000 + 8000 + 2000 + 1000 = 21000; left after required = 9000


def setup(profile):
    p = FinancialProfile.model_validate(profile)
    return p, summarize(p)


def budget(profile):
    p, f = setup(profile)
    lines, notes = build_budget(p, f)
    return {line.name: line.amount for line in lines}, lines, notes, f


def test_required_lines_keep_current_amounts():
    p, _ = setup(PROFILE)
    amounts = {line.name: line.amount for line in required_lines(p)}
    assert amounts == {"Rent": 10000, "Food": 8000, "Tuition": 2000, "Credit card (minimum payment)": 1000}


def test_high_urgency_splits_emergency_fund_and_high_interest_debt():
    # Emergency fund 5000/21000 = 0.24 months and 36% debt -> high urgency -> at least 60% to the future.
    # Wants: min(3500, 40% of 9000 = 3600) = 3500, kept in full. Future = 5500.
    # Emergency fund gets half (2750), the credit card gets the other half (2750).
    a, lines, notes, f = budget(PROFILE)
    assert a["Dining out"] == 3000 and a["Netflix"] == 500
    assert a["Emergency fund"] == 2750
    assert a["Extra payment: Credit card"] == 2750
    assert sum(a.values()) == 30000
    assert any("needs 3,000.00 a month" in n for n in notes)       # laptop goal cannot be funded on time


def test_wants_are_lowered_by_the_same_percentage():
    # Santos family from try_budgetai.py: left after required 10401, wants 7700, high urgency.
    # Wants budget = 40% of 10401 = 4160.40 -> each want lowered by 46%.
    profile = {
        "incomes": [{"name": "Juan", "monthly_amount": 32000}, {"name": "Maria", "monthly_amount": 18000}],
        "expenses": [
            {"name": "Rent", "category": "housing", "monthly_amount": 12000},
            {"name": "Groceries", "category": "food", "monthly_amount": 11000},
            {"name": "Utilities", "category": "utilities", "monthly_amount": 5499},
            {"name": "Fare", "category": "transportation", "monthly_amount": 3500},
            {"name": "Tuition", "category": "education", "monthly_amount": 3000},
            {"name": "Padala", "category": "family_support", "monthly_amount": 2000},
            {"name": "Dining out", "category": "dining_out", "monthly_amount": 4000},
            {"name": "Netflix", "category": "subscriptions", "monthly_amount": 700},
            {"name": "Shopping", "category": "shopping", "monthly_amount": 3000},
        ],
        "debts": [{"name": "Credit card", "balance": 65000, "minimum_payment": 2600, "annual_interest_rate_percent": 36}],
        "emergency_fund_balance": 10000,
    }
    a, lines, notes, f = budget(profile)
    wants = [l for l in lines if l.priority == 6]
    assert round(sum(l.amount for l in wants), 2) == 4160.40
    shares = {round(l.amount / l.needed_amount, 3) for l in wants}
    assert len(shares) == 1                                         # the same percentage for every want
    assert a["Emergency fund"] == 3120.30 and a["Extra payment: Credit card"] == 3120.30
    assert round(sum(a.values()), 2) == 50000
    assert all("Lowered by 46%" in l.reason for l in wants)


def test_medium_urgency_funds_goal_then_emergency_fund():
    # Emergency fund 40000 / 20000 = 2 months -> medium -> at least 40% to the future.
    # Wants: min(5000, 60% of 20000) = 5000 kept. Future = 15000:
    # goal monthly amount 24000 / 12 = 2000, then emergency fund toward 3 months (room 20000) gets 13000.
    a, *_ = budget({
        "incomes": [{"name": "Salary", "monthly_amount": 40000}],
        "expenses": [{"name": "Rent", "category": "housing", "monthly_amount": 12000},
                     {"name": "Food", "category": "food", "monthly_amount": 8000},
                     {"name": "Dining out", "category": "dining_out", "monthly_amount": 5000}],
        "savings_goals": [{"name": "Trip", "target_amount": 24000, "months_left": 12}],
        "emergency_fund_balance": 40000,
    })
    assert a == {"Rent": 12000, "Food": 8000, "Dining out": 5000, "Savings goal: Trip": 2000, "Emergency fund": 13000}


def test_low_urgency_keeps_wants_and_saves_the_rest():
    # Emergency fund 8 months, no debt, no goals -> low urgency. Wants kept; the rest is extra savings.
    a, *_ = budget({
        "incomes": [{"name": "Salary", "monthly_amount": 50000}],
        "expenses": [{"name": "Rent", "category": "housing", "monthly_amount": 15000},
                     {"name": "Food", "category": "food", "monthly_amount": 10000},
                     {"name": "Dining out", "category": "dining_out", "monthly_amount": 4000}],
        "emergency_fund_balance": 200000,
    })
    assert a == {"Rent": 15000, "Food": 10000, "Dining out": 4000, "Extra savings": 21000}


def test_low_interest_debt_comes_after_three_months_of_emergency_fund():
    # Emergency fund exactly 3 months (93000 / 31000). The 6% loan gets the 29000 left.
    a, lines, *_ = budget({
        "incomes": [{"name": "Salary", "monthly_amount": 60000}],
        "expenses": [{"name": "Rent", "category": "housing", "monthly_amount": 20000},
                     {"name": "Food", "category": "food", "monthly_amount": 10000}],
        "debts": [{"name": "Pag-IBIG loan", "balance": 100000, "minimum_payment": 1000, "annual_interest_rate_percent": 6}],
        "emergency_fund_balance": 93000,
    })
    assert a["Extra payment: Pag-IBIG loan"] == 29000
    assert next(l for l in lines if l.name == "Extra payment: Pag-IBIG loan").priority == 5


def test_unknown_interest_rate_is_treated_as_high():
    p, f = setup({**PROFILE, "debts": [{"name": "Loan app", "balance": 5000, "minimum_payment": 500}],
                  "emergency_fund_balance": 100000})
    assert urgency(p, f)[0] == "high"


def test_deficit_funds_essentials_first_then_shares_obligations():
    a, lines, notes, _ = budget({**PROFILE, "incomes": [{"name": "Salary", "monthly_amount": 19500}]})
    assert a["Rent"] == 10000 and a["Food"] == 8000                 # essentials (18000) in full
    assert a["Tuition"] == 1000                                     # 1500 left for 3000 of obligations = 50%
    assert a["Credit card (minimum payment)"] == 500
    assert sum(a.values()) == 19500
    assert "Dining out" not in a and "Emergency fund" not in a
    assert any("50%" in n for n in notes)


def test_deficit_where_even_essentials_are_not_covered():
    a, lines, notes, _ = budget({**PROFILE, "incomes": [{"name": "Salary", "monthly_amount": 9000}]})
    assert a["Rent"] == 5000 and a["Food"] == 4000                  # 50% each
    assert a["Tuition"] == 0 and a["Credit card (minimum payment)"] == 0
    assert any("Nothing is left" in n for n in notes)


def test_deficit_shares_add_up_to_the_exact_centavo():
    a, *_ = budget({"incomes": [{"name": "Salary", "monthly_amount": 16000}],
                    "expenses": [{"name": n, "category": c, "monthly_amount": v} for n, c, v in [
                        ("A", "housing", 5500), ("B", "food", 7500), ("C", "utilities", 2200),
                        ("D", "utilities", 300), ("E", "transportation", 2000)]]})
    assert round(sum(a.values()), 2) == 16000


def test_no_expenses_leaves_income_unallocated():
    a, lines, notes, _ = budget({"incomes": [{"name": "Salary", "monthly_amount": 25000}]})
    assert lines == []
    assert "No expenses are listed" in notes[0]


def random_household(rng):
    categories = ["housing", "food", "utilities", "transportation", "education", "family_support",
                  "dining_out", "shopping", "subscriptions", "entertainment", "other"]
    return {
        "incomes": [{"name": f"Income {i}", "monthly_amount": rng.randint(5000, 80000)} for i in range(rng.randint(1, 3))],
        "expenses": [{"name": f"Expense {i}", "category": rng.choice(categories),
                      "monthly_amount": rng.randint(0, 20000)} for i in range(rng.randint(1, 12))],
        "debts": [{"name": f"Debt {i}", "balance": rng.randint(0, 100000), "minimum_payment": rng.randint(0, 5000),
                   "annual_interest_rate_percent": rng.choice([None, 5, 24, 120])} for i in range(rng.randint(0, 3))],
        "savings_goals": [{"name": f"Goal {i}", "target_amount": rng.randint(1000, 90000),
                           "current_amount": rng.randint(0, 5000),
                           "months_left": rng.choice([None, 3, 12])} for i in range(rng.randint(0, 2))],
        "emergency_fund_balance": rng.randint(0, 150000),
    }


def test_rules_hold_for_300_random_households():
    """Never above income, never negative, every peso assigned, and the same input gives the same budget."""
    for seed in range(300):
        profile = random_household(random.Random(seed))
        a, lines, _, f = budget(profile)
        total = round(sum(line.amount for line in lines), 2)
        assert total <= f.planning_income + 0.01, f"household {seed}: budget {total} > income {f.planning_income}"
        assert all(line.amount >= 0 for line in lines), f"household {seed}: negative amount"
        if not f.is_deficit:
            assert abs(total - f.planning_income) <= 0.01, f"household {seed}: {f.planning_income - total} unassigned"
        again, *_ = budget(profile)
        assert again == a, f"household {seed}: different budget for the same input"


def test_urgency_uses_the_exact_emergency_fund_ratio():
    # 61500 / 61550 = 0.999 months: shown as 1.00, but still under 1 month, so urgency is high.
    p, f = setup({"incomes": [{"name": "Salary", "monthly_amount": 80000}],
                  "expenses": [{"name": "Rent", "category": "housing", "monthly_amount": 61550},
                               {"name": "Games", "category": "entertainment", "monthly_amount": 5000}],
                  "emergency_fund_balance": 61500})
    assert f.emergency_fund_months == 1.0
    assert urgency(p, f)[0] == "high"
