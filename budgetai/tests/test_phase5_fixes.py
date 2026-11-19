"""Phase 5 regression tests. Each test reproduces a reported issue and must keep passing.

1. A debt whose minimum payment is above its balance.
2. Amounts with fractions of a centavo, such as 0.006.
3. Incomplete household data: debts known but no expenses; irregular income without a low month.
"""
import json
import random
from decimal import Decimal
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from budgeting_engine.insights import action_steps, key_facts
from budgeting_engine.planner import make_plan
from budgeting_engine.validation import clean_ai_advice, find_missing_data
from model.budgeting_ai import BudgetAI
from model.schemas import AIBudgetAdvice, FinancialProfile


class FakeClient:
    def __init__(self, reply):
        self.reply, self.calls = reply, []

    def chat(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(message=SimpleNamespace(content=self.reply))


def ai_reply(summary="A summary.", questions=()):
    return json.dumps({"summary": summary, "savings_advice": "Save.", "spending_flags": [], "warnings": [],
                       "missing_information": list(questions)})


def facts_and_steps(profile):
    p = FinancialProfile.model_validate(profile)
    plan = make_plan(p)
    return plan, key_facts(p, plan.facts), plan.action_steps


# ===================================================== 1. debt minimum above the balance
HELD = {
    "incomes": [{"name": "Salary", "monthly_amount": 30000}],
    "expenses": [{"name": "Rent", "category": "housing", "monthly_amount": 10000},
                 {"name": "Food", "category": "food", "monthly_amount": 8000}],
    "debts": [{"name": "Store credit", "balance": 100, "minimum_payment": 500, "annual_interest_rate_percent": 24}],
    "household_size": 2,
}


def test_debt_minimum_above_balance_is_not_budgeted():
    plan = make_plan(HELD)
    names = [line.name for line in plan.budget]
    assert "Store credit (minimum payment)" not in names
    assert not any(name.startswith("Extra payment: Store credit") for name in names)
    assert plan.facts.minimum_debt_payments == 0
    assert plan.facts.required_spending == 18000
    assert plan.total_allocated <= plan.facts.planning_income


def test_debt_minimum_above_balance_is_flagged_and_asked_about():
    plan = make_plan(HELD)
    question = [m for m in plan.missing_data if "Store credit" in m]
    assert question and "500.00" in question[0] and "100.00" in question[0]
    assert any("Store credit" in n and "not budgeted" in n for n in plan.rule_notes)
    assert any("Confirm the balance and minimum payment of your Store credit" in s for s in plan.action_steps)


def test_minimum_equal_to_balance_is_still_budgeted():
    plan = make_plan({**HELD, "debts": [{"name": "Store credit", "balance": 500, "minimum_payment": 500,
                                         "annual_interest_rate_percent": 24}]})
    assert any(line.name == "Store credit (minimum payment)" and line.amount == 500 for line in plan.budget)
    assert not any("Store credit" in m for m in plan.missing_data)


def test_held_debt_question_survives_the_ai_cleanup():
    # The AI asks one unrelated question, so Python's question about the debt must be used instead.
    ai = BudgetAI(client=FakeClient(ai_reply(questions=["Is your rent paid monthly?"])))
    result = ai.generate_budget(HELD)
    assert any("Store credit" in q for q in result.advice.missing_information)


# ===================================================== 2. fractions of a centavo
def test_amounts_round_to_the_nearest_centavo_half_up():
    p = FinancialProfile.model_validate({
        "incomes": [{"name": "Salary", "monthly_amount": 20000.006}],
        "expenses": [{"name": "Load", "category": "utilities", "monthly_amount": 0.006},
                     {"name": "Tiny", "category": "other", "monthly_amount": 0.004},
                     {"name": "Half", "category": "food", "monthly_amount": 0.005}],
        "emergency_fund_balance": 2.675,
    })
    assert p.incomes[0].monthly_amount == 20000.01
    assert [e.monthly_amount for e in p.expenses] == [0.01, 0.0, 0.01]
    assert p.emergency_fund_balance == 2.68          # half up; plain float rounding would give 2.67


@pytest.mark.parametrize("amount", [0.004, -0.004, 0])
def test_income_that_rounds_to_zero_or_below_is_rejected(amount):
    with pytest.raises(ValidationError):
        FinancialProfile.model_validate({"incomes": [{"name": "Salary", "monthly_amount": amount}]})


def _displayed(value):
    return Decimal(f"{value:.2f}")


def test_displayed_lines_add_up_to_the_displayed_total_for_200_random_households():
    categories = ["housing", "food", "utilities", "transportation", "education", "dining_out", "shopping", "other"]
    for seed in range(200):
        rng = random.Random(seed)
        three_decimals = lambda low, high: round(rng.uniform(low, high), 3)
        profile = {
            "incomes": [{"name": f"Income {i}", "monthly_amount": three_decimals(5000, 60000)} for i in range(rng.randint(1, 2))],
            "expenses": [{"name": f"Expense {i}", "category": rng.choice(categories),
                          "monthly_amount": three_decimals(0, 9000)} for i in range(rng.randint(1, 9))],
            "debts": [{"name": "Card", "balance": three_decimals(1000, 50000), "minimum_payment": three_decimals(0, 900),
                       "annual_interest_rate_percent": 36}] if rng.random() < .5 else [],
            "emergency_fund_balance": three_decimals(0, 40000),
        }
        plan = make_plan(profile)
        for line in plan.budget:
            assert abs(line.amount * 100 - round(line.amount * 100)) < 1e-6, f"household {seed}: {line.amount} is not whole centavos"
        assert sum(_displayed(l.amount) for l in plan.budget) == _displayed(plan.total_allocated), f"household {seed}"
        assert _displayed(plan.facts.planning_income) - _displayed(plan.total_allocated) == _displayed(plan.unallocated)
        assert plan.total_allocated <= plan.facts.planning_income + 1e-9


# ===================================================== 3. incomplete household data
PARTIAL = {
    "incomes": [{"name": "Salary", "monthly_amount": 25000}],
    "debts": [{"name": "SSS salary loan", "balance": 20000, "minimum_payment": 1500, "annual_interest_rate_percent": 10}],
    "household_size": 3,
}


def test_known_debt_payment_without_expenses_is_a_labelled_partial_plan():
    plan = make_plan(PARTIAL)
    assert plan.is_partial
    assert [(l.name, l.amount) for l in plan.budget] == [("SSS salary loan (minimum payment)", 1500)]
    assert "Partial plan" in plan.budget[0].reason
    note = next(n for n in plan.rule_notes if n.startswith("Partial plan"))
    assert "1,500.00" in note and "23,500.00" in note
    assert plan.unallocated == 23500


def test_partial_plan_facts_and_steps_agree_with_the_budget():
    plan, facts, steps = facts_and_steps(PARTIAL)
    text = " ".join(facts)
    assert "no budget can be made" not in text
    assert "partial plan" in text.lower() and "1500" in text
    assert steps[0] == "Pay the minimum on your SSS salary loan (PHP 1,500.00) as usual."
    assert steps[-1].startswith("List your monthly expenses")


def test_partial_plan_explanation_agrees_with_the_plan():
    ai = BudgetAI(client=FakeClient(ai_reply(summary="No budget can be made yet.")))
    result = ai.generate_budget(PARTIAL)
    assert result.is_partial
    assert result.advice.summary.startswith("This is a partial plan")
    assert "1,500.00" in result.advice.summary
    assert any(c.startswith("NOTE: partial plan") for c in result.checks)
    prompt = ai.client.calls[0]["messages"][1]["content"]
    assert "Partial plan" in prompt


def test_no_expenses_and_no_debts_is_not_a_partial_plan():
    plan = make_plan({"incomes": [{"name": "Salary", "monthly_amount": 25000}]})
    assert not plan.is_partial and plan.budget == []


IRREGULAR_NO_LOW = {
    "incomes": [{"name": "Freelance projects", "monthly_amount": 30000, "regular": False}],
    "expenses": [{"name": "Rent", "category": "housing", "monthly_amount": 9000},
                 {"name": "Food", "category": "food", "monthly_amount": 7000}],
    "household_size": 1,
}


def test_irregular_income_without_low_month_says_the_stated_amount_was_used():
    plan, facts, _ = facts_and_steps(IRREGULAR_NO_LOW)
    assert plan.facts.planning_income == 30000
    assert "stated monthly amount" in facts[0] and "30000" in facts[0]
    assert "uses a low month" not in facts[0]
    assert any("stated monthly amount (30,000.00)" in n for n in plan.rule_notes)
    assert any("planning used the stated monthly amount" in m for m in plan.missing_data)


def test_irregular_income_with_low_month_is_still_explained_as_a_low_month():
    profile = {**IRREGULAR_NO_LOW, "incomes": [{"name": "Freelance projects", "monthly_amount": 30000,
                                                "regular": False, "minimum_monthly_amount": 18000}]}
    plan, facts, _ = facts_and_steps(profile)
    assert plan.facts.planning_income == 18000
    assert "uses a low month" in facts[0]
    assert not any("stated monthly amount" in n for n in plan.rule_notes)


def test_questions_cover_every_missing_item_or_python_asks_them():
    p = FinancialProfile.model_validate(IRREGULAR_NO_LOW)
    missing = find_missing_data(p)
    advice = AIBudgetAdvice(summary="", savings_advice="", spending_flags=[], warnings=[],
                            missing_information=["What is your favorite food?"])
    cleaned, _ = clean_ai_advice(advice, p, [], missing)
    assert cleaned.missing_information == missing
