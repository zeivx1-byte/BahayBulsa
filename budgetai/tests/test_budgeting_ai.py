"""Tests for model/budgeting_ai.py using a FAKE model.

The fake client returns prepared answers instead of calling Ollama, so these
tests are fast, need no GPU, and check BudgetAI's own logic: the request it
builds, retries, the cleaned advice, the result and the error messages.
The real model is tried with:  python try_budgetai.py
"""
import json
import re
from types import SimpleNamespace

import ollama
import pytest
from pydantic import ValidationError

import config
from model.budgeting_ai import (
    ANSWER_SCHEMA, BudgetAI, BudgetAIError, InvalidModelResponseError, ModelUnavailableError,
    load_instructions,
)

PROFILE = {
    "incomes": [{"name": "Salary", "monthly_amount": 30000}],
    "expenses": [
        {"name": "Rent", "category": "housing", "monthly_amount": 10000},
        {"name": "Food", "category": "food", "monthly_amount": 8000},
        {"name": "Dining out", "category": "dining_out", "monthly_amount": 3000},
    ],
    "debts": [{"name": "Credit card", "balance": 20000, "minimum_payment": 1000, "annual_interest_rate_percent": 36}],
    "household_size": 2,
}
# required 19000, left 11000, no emergency fund + 36% debt -> high urgency:
# wants min(3000, 40% of 11000) = 3000 kept; future 8000 -> emergency fund 4000, credit card 4000.
DEFICIT_PROFILE = {**PROFILE, "incomes": [{"name": "Salary", "monthly_amount": 15000}]}
NO_EXPENSES = {"incomes": [{"name": "Salary", "monthly_amount": 25000}]}


def advice(flags=(), questions=(), warnings=(), *, summary="A test summary.", savings="Save regularly."):
    """Build an AI answer in the required JSON format."""
    return json.dumps({
        "summary": summary,
        "savings_advice": savings,
        "spending_flags": list(flags),
        "warnings": list(warnings),
        "missing_information": list(questions),
    })


GOOD = advice()


class FakeClient:
    """Pretends to be ollama.Client: returns prepared replies in order."""

    def __init__(self, *replies):
        self.replies = list(replies)
        self.calls = []

    def chat(self, **kwargs):
        self.calls.append(kwargs)
        item = self.replies.pop(0)
        if isinstance(item, Exception):
            raise item
        return SimpleNamespace(message=SimpleNamespace(content=item))


def make_ai(*replies):
    client = FakeClient(*replies)
    return BudgetAI(client=client), client


# ---------------------------------------------------------------- normal use
def test_budget_comes_from_python_rules_and_advice_from_the_ai():
    ai, _ = make_ai(GOOD)
    result = ai.generate_budget(PROFILE)
    budget = {line.name: (line.amount, line.source) for line in result.budget}
    assert budget["Rent"] == (10000, "required")
    assert budget["Credit card (minimum payment)"] == (1000, "required")
    assert budget["Emergency fund"] == (4000, "rule")
    assert budget["Extra payment: Credit card"] == (4000, "rule")
    assert budget["Dining out"] == (3000, "rule")
    assert result.total_allocated == 30000 and result.unallocated == 0
    assert result.within_income is True and result.attempts == 1
    assert result.advice.summary.startswith("Monthly planning income of PHP 30,000.00 covers PHP 19,000.00")
    assert result.action_steps[0] == "On payday, move PHP 4,000.00 to a separate savings account for your emergency fund."
    assert result.checks[0].startswith("OK")


def test_request_settings_sent_to_ollama():
    ai, client = make_ai(GOOD)
    ai.generate_budget(PROFILE)
    call = client.calls[0]
    assert call["model"] == config.settings.model_name
    assert call["format"]["required"] == ANSWER_SCHEMA["required"]
    assert call["think"] is False
    assert call["options"]["temperature"] == config.settings.temperature
    assert call["options"]["num_ctx"] == config.settings.context_tokens


def test_model_reply_format_limits_warnings_and_expense_suggestions():
    ai, client = make_ai(GOOD)
    ai.generate_budget(DEFICIT_PROFILE)
    schema = client.calls[0]["format"]
    assert schema["properties"]["warnings"]["items"]["enum"][0] == (
        "Monthly planning income is PHP 4000.00 short of required spending."
    )
    assert schema["properties"]["missing_information"]["maxItems"] == 0
    assert schema["properties"]["spending_flags"]["maxItems"] == 1
    assert schema["$defs"]["SpendingFlag"]["properties"]["expense_name"]["enum"] == ["Food"]
    assert schema["$defs"]["SpendingFlag"]["properties"]["reason"]["enum"] == ["Compare prices before buying."]
    assert "enum" not in ANSWER_SCHEMA["properties"]["warnings"]["items"]


def test_prompt_contains_facts_final_budget_and_notes():
    ai, client = make_ai(GOOD)
    ai.generate_budget(PROFILE)
    system, user = client.calls[0]["messages"]
    assert "HOW THE WORK IS SHARED" in system["content"] and "{currency}" not in system["content"]
    text = user["content"]
    assert "SITUATION: stable" in text
    assert "SUMMARY FOCUS: Required spending is covered." in text
    assert "This is a partial plan" not in text
    assert "- Required spending (essentials, obligations and minimum debt payments): 19000." in text
    assert "- There is no emergency fund yet." in text
    assert "- Debt: Credit card, balance 20000, minimum payment 1000, 36% a year (high interest)." in text
    assert "- Rent | funded 10000\n" in text
    assert "- Emergency fund | funded 4000\n" in text
    assert "Total funded: 30000 of 30000 income\nUnallocated after this plan: 0" in text
    assert "ADJUSTABLE EXPENSES:\n- Food | current 8000 | reason: Compare prices before buying.\n\n" in text
    assert "HOUSEHOLD: 2 people" in text
    assert "PYTHON NOTES:\n- Because the emergency fund covers less than 1 month" in text
    assert "at least 60% of the 11000.00 left" in text                   # comma removed for the AI
    assert "MISSING DATA:\nnone" in text


def test_prompt_numbers_never_use_thousands_commas():
    """A comma inside a number once made the model write 4 instead of 4,000 in its JSON."""
    for profile in (PROFILE, DEFICIT_PROFILE, NO_EXPENSES):
        ai, client = make_ai(GOOD)
        ai.generate_budget(profile)
        prompt = client.calls[0]["messages"][1]["content"]
        assert not re.search(r"\d,\d{3}", prompt)


def test_deficit_prompt_shows_how_much_of_each_need_is_covered():
    ai, client = make_ai(GOOD)
    result = ai.generate_budget(DEFICIT_PROFILE)
    text = client.calls[0]["messages"][1]["content"]
    assert "- Rent | funded 8333.34 | needed 10000" in text
    assert "Credit card (minimum payment) | funded 0 | needed 1000" in text
    assert "VERIFIED WARNINGS:\n- Monthly planning income is PHP 4000.00 short of required spending." in text
    assert result.total_allocated == 15000 and result.unfunded == 4000
    assert any(c.startswith("DEFICIT") for c in result.checks)


def test_ai_advice_is_cleaned_against_the_budget():
    messy = advice(
        flags=[
            {"expense_name": "Rent (housing)", "current_amount": 10000, "suggested_amount": 0, "reason": "cut"},
            {"expense_name": "Food", "current_amount": 8000, "suggested_amount": 7000, "reason": "buy in bulk"},
            {"expense_name": "Dining out", "current_amount": 3000, "suggested_amount": 1000, "reason": "less"},
            {"expense_name": "No expenses listed", "current_amount": 0, "suggested_amount": 0, "reason": "?"},
        ],
        questions=["Is your rent monthly?"],
    )
    ai, _ = make_ai(messy)
    result = ai.generate_budget(PROFILE)
    flags = {f.expense_name: (f.current_amount, f.suggested_amount) for f in result.advice.spending_flags}
    assert flags == {"Food": (8000, 7000)}             # dining out is kept in full by the rules, so no flag
    assert result.advice.missing_information == []     # nothing is missing
    assert any("Removed 3 AI spending flag(s)" in n for n in result.rule_notes)


def test_no_expenses_gives_questions_not_a_budget():
    ai, _ = make_ai(GOOD)                               # the AI asks nothing...
    result = ai.generate_budget(NO_EXPENSES)
    assert result.budget == [] and result.unallocated == 25000
    assert len(result.advice.missing_information) == 2  # ...so Python's own questions are used


def test_deficit_explanation_uses_the_exact_shortfall_from_the_plan():
    reply = advice(summary="All your bills are covered.", savings="Skip debt payments and save PHP 500.",
                   warnings=["This month's shortfall is PHP 8,000."])
    ai, _ = make_ai(reply)
    result = ai.generate_budget(DEFICIT_PROFILE)
    assert result.advice.summary.startswith("Monthly income does not cover required spending.")
    assert result.advice.warnings[0] == "Monthly planning income is PHP 4,000.00 short of required spending."
    assert "Skip debt payments" not in result.advice.savings_advice
    assert "8,000" not in " ".join(result.advice.warnings)


def test_weak_fund_warning_and_savings_action_use_verified_plan():
    reply = advice(summary="Your emergency fund is healthy.", savings="Move another PHP 1,000 to savings.")
    ai, _ = make_ai(reply)
    result = ai.generate_budget(PROFILE)
    assert "healthy" not in result.advice.summary
    assert result.advice.savings_advice == "This month, move PHP 4,000.00 to your emergency fund as shown in the budget."
    assert result.advice.warnings == ["Your emergency fund covers less than one month of required spending."]


def test_fully_allocated_heldout_budget_rejects_unfunded_extra_payment():
    # BA-0036. The original reply confused money left after essentials with unallocated income.
    with (config.settings.dataset_dir / "test_scenarios.jsonl").open(encoding="utf-8") as scenarios:
        scenario = next(case for case in map(json.loads, scenarios) if case["id"] == "BA-0036")
    savings = (
        "Since you have plenty of money left over after covering essentials, focus on increasing the 'Extra payment' "
        "for your credit card. Instead of just making the minimum payment, add an additional amount each month to "
        "aggressively reduce the balance and lower your interest rate before the wedding date."
    )
    ai, _ = make_ai(advice(savings=savings))
    result = ai.generate_budget(scenario["profile"])
    assert result.unallocated == 0
    assert result.advice.savings_advice == (
        "All planning income is assigned in this budget. Follow its allocations. "
        "Before increasing savings or debt payments, reduce a planned cost or add income."
    )


@pytest.mark.parametrize("savings", [
    "If you cut dining out, put the spare cash toward your wedding goal.",
    "Increase your savings by reducing the dining out budget first.",
    "Move your planned savings on payday.",
    "There is no spare cash this month. Make the planned extra credit card payment on payday.",
])
def test_fully_allocated_budget_preserves_grounded_savings_advice(savings):
    ai, _ = make_ai(advice(savings=savings))
    result = ai.generate_budget(PROFILE)
    assert result.unallocated == 0
    assert result.advice.savings_advice == savings


def test_future_funding_suggestion_does_not_excuse_a_false_spare_cash_claim():
    savings = "Since you have extra cash, increase your savings by reducing spending on dining out."
    ai, _ = make_ai(advice(savings=savings))
    assert ai.generate_budget(PROFILE).advice.savings_advice.startswith("All planning income is assigned")


@pytest.mark.parametrize("claim", ["Your emergency fund has PHP 4,000 saved.",
                                  "Your emergency fund already covers one month of bills.",
                                  "Your current budget has a shortfall."])
def test_unverified_summary_claims_are_replaced_with_planner_facts(claim):
    ai, _ = make_ai(advice(summary=claim, savings="Move the planned savings on payday."))
    result = ai.generate_budget(PROFILE)
    assert result.advice.summary.startswith("Monthly planning income of PHP 30,000.00 covers PHP 19,000.00")
    assert result.advice.savings_advice == "Move the planned savings on payday."


def test_irregular_income_and_held_debt_caveats_appear_in_final_advice():
    profile = {**PROFILE,
               "incomes": [{"name": "Freelance work", "monthly_amount": 30000, "regular": False}],
               "debts": [{"name": "Store credit", "balance": 200, "minimum_payment": 500}]}
    ai, _ = make_ai(advice(summary="You have a stable income."))
    result = ai.generate_budget(profile)
    assert "stable income" not in result.advice.summary
    assert any("stated monthly amount (30,000.00)" in warning for warning in result.advice.warnings)
    assert any("Store credit' is not budgeted" in warning for warning in result.advice.warnings)


def test_fund_coverage_is_unavailable_without_required_spending():
    profile = {**PROFILE, "expenses": [PROFILE["expenses"][2]], "debts": [], "emergency_fund_balance": 1000}
    ai, client = make_ai(GOOD)
    result = ai.generate_budget(profile)
    assert result.facts.emergency_fund_months is None
    assert "Coverage in months cannot be calculated" in client.calls[0]["messages"][1]["content"]
    assert not result.advice.warnings


def test_partial_deficit_warning_refers_only_to_known_debt_payments():
    profile = {"incomes": [{"name": "Salary", "monthly_amount": 500}],
               "debts": [{"name": "Loan", "balance": 3000, "minimum_payment": 1500}]}
    ai, client = make_ai(GOOD)
    result = ai.generate_budget(profile)
    assert result.is_partial and result.advice.summary.startswith("This is a partial plan")
    assert "PHP 1000.00 short of known minimum debt payments" in client.calls[0]["messages"][1]["content"]
    assert result.advice.warnings == ["Monthly planning income is PHP 1,000.00 short of known minimum debt payments."]


# ---------------------------------------------------------------- retries
def test_invalid_json_is_retried_with_some_variety():
    ai, client = make_ai("this is not json", GOOD)
    result = ai.generate_budget(PROFILE)
    assert result.attempts == 2
    assert client.calls[1]["options"]["temperature"] >= 0.3


def test_repeated_decoding_stalls_fall_back_to_the_standard_reply_format():
    stalled = ollama.ResponseError("prediction aborted, token repeat limit reached", 500)
    ai, client = make_ai(stalled, stalled, GOOD)
    result = ai.generate_budget(PROFILE)
    assert result.attempts == 3
    assert client.calls[0]["format"] == client.calls[1]["format"]
    assert client.calls[2]["format"] == ANSWER_SCHEMA
    assert result.advice.warnings == ["Your emergency fund covers less than one month of required spending."]


def test_wrong_shape_is_retried():
    ai, _ = make_ai(json.dumps({"summary": "missing fields"}), GOOD)
    assert ai.generate_budget(PROFILE).attempts == 2


def test_gives_up_after_three_invalid_answers():
    ai, _ = make_ai("nope", "nope", "nope")
    with pytest.raises(InvalidModelResponseError, match="3 times"):
        ai.generate_budget(PROFILE)


# ---------------------------------------------------------------- errors
def test_ollama_not_running():
    ai, _ = make_ai(ConnectionError("refused"))
    with pytest.raises(ModelUnavailableError, match="Open Ollama"):
        ai.generate_budget(PROFILE)


def test_model_not_downloaded():
    ai, _ = make_ai(ollama.ResponseError("model not found", 404))
    with pytest.raises(ModelUnavailableError, match="ollama pull"):
        ai.generate_budget(PROFILE)


def test_timeout_or_other_failure_gives_clear_message():
    ai, _ = make_ai(TimeoutError("timed out"))
    with pytest.raises(BudgetAIError, match="BUDGETAI_TIMEOUT_SECONDS"):
        ai.generate_budget(PROFILE)


def test_invalid_profile_is_rejected_before_calling_the_model():
    ai, client = make_ai(GOOD)
    with pytest.raises(ValidationError):
        ai.generate_budget({"incomes": []})
    assert client.calls == []


def test_missing_instructions_file(tmp_path):
    with pytest.raises(BudgetAIError, match="not found"):
        load_instructions(tmp_path / "missing.txt", "PHP")
