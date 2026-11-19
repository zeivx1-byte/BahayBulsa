"""Test budget chat with real Python planning and a fake model transport."""

import json
from dataclasses import replace

import pytest
from pydantic import ValidationError

import config
from model.ai_service import InvalidModelResponseError, ModelReply, ModelUnavailableError
from model.budget_chat import ScenarioError
from model.budgeting_ai import BudgetAI
from model.schemas import ChatRequest


PROFILE = {
    "incomes": [{"name": "Salary", "monthly_amount": 30000}],
    "expenses": [
        {"name": "Rent", "category": "housing", "monthly_amount": 10000},
        {"name": "Groceries", "category": "food", "monthly_amount": 8000},
    ],
    "household_size": 2,
}


class FakeModel:
    def __init__(self):
        self.reply = json.dumps({"topic": "line", "line_index": 2})
        self.calls = []

    def chat(self, **options):
        self.calls.append(options)
        if isinstance(self.reply, Exception):
            raise self.reply
        return ModelReply(self.reply)


@pytest.fixture
def chat():
    transport = FakeModel()
    settings = replace(config.settings, currency="PHP")
    with BudgetAI(settings=settings, client=transport) as ai:
        yield ai, transport


def ask(ai, payload):
    return ai.respond_to_chat(ChatRequest.model_validate(payload))


def amounts(plan):
    return {line.name: line.amount for line in plan.budget}


def test_chat_explains_a_verified_budget_line_with_conversation_context(chat):
    ai, transport = chat
    history = [
        {"role": "user", "content": "What did you allocate for savings?"},
        {"role": "assistant", "content": "The budget includes an emergency fund."},
    ]

    response = ask(ai, {
        "profile": PROFILE,
        "message": "Could you expand on your answer?",
        "history": history,
    })

    assert response.scenario is None
    assert amounts(response.plan)["Emergency fund"] == 12000
    assert "emergency fund" in response.reply.lower()
    assert "12,000" in response.reply
    assert transport.calls
    prompt = json.dumps(transport.calls[0]["messages"])
    assert "What did you allocate for savings?" in prompt
    assert "The budget includes an emergency fund." in prompt
    assert "Could you expand on your answer?" in prompt
    assert "Emergency fund" in prompt


def test_chat_gives_conversational_plan_adjustment_with_bounded_context(chat):
    ai, transport = chat
    transport.reply = json.dumps({
        "topic": "conversation",
        "reply": "Review your optional spending first. Which expense could you trim to fund the increase?",
    })
    response = ask(ai, {
        "profile": PROFILE,
        "message": "How could I increase my savings allocation?",
        "history": [{
            "role": "user", "content": "previous long content " + "x" * 250 + " My email is household@example.com.",
        }, {"role": "assistant", "content": "We planned a contribution to the emergency fund."}],
    })
    assert response.reply == "Review your optional spending first. Which expense could you trim to fund the increase?"
    assert amounts(response.plan)["Emergency fund"] == 12000 and response.scenario is None
    assert len(transport.calls) == 1
    context = json.loads(transport.calls[0]["messages"][1]["content"])
    assert context["planned_budget"]["unallocated_income"] == 0
    assert "emergency fund" in context["history"][1]["content"]
    assert "household@example.com" not in context["history"][0]["content"]
    assert "previous long content" not in context["history"][0]["content"]


def test_chat_replaces_unverified_amounts_in_conversational_replies(chat):
    ai, transport = chat
    transport.reply = json.dumps({"topic": "conversation", "reply": "Your recorded savings grew by PHP 55,555."})
    response = ask(ai, {"profile": PROFILE, "message": "How could I save more?"})
    assert "55,555" not in response.reply


def test_general_budgeting_question_excludes_household_details(chat):
    ai, transport = chat
    transport.reply = json.dumps({"topic": "conversation", "reply": "A budget is a spending and savings plan."})
    response = ask(ai, {"profile": PROFILE, "message": "What is a budget?"})
    assert response.reply == "A budget is a spending and savings plan."
    context = json.loads(transport.calls[0]["messages"][1]["content"])
    assert context["budget_lines"] == []
    assert "planned_budget" not in context


def test_chat_answers_named_allocation_directly(chat):
    ai, transport = chat
    response = ask(ai, {
        "profile": PROFILE, "message": "Why did you put money in my emergency fund?",
    })
    assert "PHP 12,000.00" in response.reply
    assert "Emergency fund" in response.reply
    assert not transport.calls


def test_chat_resolves_a_follow_up_to_a_named_amount(chat):
    ai, transport = chat
    response = ask(ai, {
        "profile": PROFILE, "message": "Why that amount?",
        "history": [{"role": "assistant", "content": "The plan allocates PHP 12,000 to Emergency fund."}],
    })
    assert "PHP 12,000.00" in response.reply
    assert not transport.calls


@pytest.mark.parametrize("message", [
    "What if my rent goes up by ₱2,000?",
    "What if my rent rises to PHP 12,000 per month?",
])
def test_natural_language_change_compares_budgets_without_changing_original(chat, message):
    ai, transport = chat

    response = ask(ai, {
        "profile": PROFILE,
        "message": message,
    })

    scenario = response.scenario
    assert scenario is not None
    assert scenario.expense_name == "Rent"
    assert scenario.previous_monthly_amount == 10000
    assert scenario.new_monthly_amount == 12000
    assert amounts(response.plan) == {"Rent": 10000, "Groceries": 8000, "Emergency fund": 12000}
    assert amounts(scenario.plan) == {"Rent": 12000, "Groceries": 8000, "Emergency fund": 10000}
    assert scenario.plan.total_allocated == 30000
    assert not transport.calls

    follow_up = ask(ai, {"profile": PROFILE, "message": "Why the emergency fund?"})
    assert follow_up.plan == response.plan


@pytest.mark.parametrize(("operation", "amount", "expected"), [
    ("set", 12500, 12500),
    ("increase", 1500, 11500),
    ("decrease", 1500, 8500),
    ("decrease", 0.005, 9999.99),
])
def test_structured_changes_use_exact_amounts_and_case_insensitive_expense_names(
    chat, operation, amount, expected,
):
    ai, transport = chat
    response = ask(ai, {
        "profile": PROFILE,
        "message": "Compare this change with my current budget.",
        "change": {"expense_name": "rEnT", "operation": operation, "amount": amount},
    })

    assert response.scenario is not None
    assert response.scenario.expense_name == "Rent"
    assert response.scenario.new_monthly_amount == expected
    assert amounts(response.plan)["Rent"] == 10000
    assert amounts(response.scenario.plan)["Rent"] == expected
    assert not transport.calls


@pytest.mark.parametrize("message", [
    "What if my rent goes up?",
    "What if my rent increases by 2000 and groceries decrease by 1000?",
    "What if my rent goes up by USD 2,000?",
    "What if my rent goes up to 8000?",
])
def test_ambiguous_natural_language_changes_do_not_change_the_budget(chat, message):
    ai, _ = chat
    response = ask(ai, {"profile": PROFILE, "message": message})

    assert response.scenario is None
    assert amounts(response.plan)["Rent"] == 10000
    assert amounts(response.plan)["Groceries"] == 8000
    assert response.reply


@pytest.mark.parametrize(("profile", "change"), [
    (PROFILE, {"expense_name": "Rent", "operation": "decrease", "amount": 10001}),
    (PROFILE, {"expense_name": "None of the listed expenses", "operation": "set", "amount": 1}),
    ({**PROFILE, "expenses": [
        *PROFILE["expenses"], {"name": "rent", "category": "housing", "monthly_amount": 1000},
    ]}, {"expense_name": "Rent", "operation": "increase", "amount": 1}),
])
def test_invalid_or_ambiguous_structured_changes_are_rejected(chat, profile, change):

    ai, transport = chat
    with pytest.raises(ScenarioError):
        ask(ai, {"profile": profile, "message": "Compare these", "change": change})
    assert not transport.calls


@pytest.mark.parametrize("extra", [
    {"message": "   "},
    {"message": "x" * 1001},
    {"history": [{"role": "system", "content": "private-test-marker"}]},
])
def test_chat_request_rejects_invalid_messages_and_roles(chat, extra):

    ai, transport = chat
    with pytest.raises(ValidationError):
        ask(ai, {"profile": PROFILE, "message": "Explain my budget", **extra})
    assert not transport.calls


@pytest.mark.parametrize(("model_reply", "error_type"), [
    (ConnectionError("private-model-error"), ModelUnavailableError),
    ("private-model-error", InvalidModelResponseError),
])
def test_chat_reports_provider_failures_without_echoing_model_output(chat, model_reply, error_type):

    ai, transport = chat
    transport.reply = model_reply
    with pytest.raises(error_type) as caught:
        ask(ai, {"profile": PROFILE, "message": "How much is still unallocated?"})
    assert "private-model-error" not in str(caught.value)


def test_records_are_scoped_to_each_request_and_separate_from_the_plan(chat):
    ai, transport = chat
    transport.reply = json.dumps({"topic": "spending"})
    records = {
        "coverage_start": "2026-11-01",
        "as_of": "2026-11-19",
        "transactions": [
            {"posted_on": "2026-11-02", "name": "Private test entry", "category": "housing", "amount": 4321.23},
        ],
    }
    question = {"profile": PROFILE, "message": "How much did I spend this month?"}
    first = ask(ai, {**question, "records": records})
    second = ask(ai, question)
    assert "PHP 4,321.23" in first.reply
    assert "Send dated expense records" in second.reply
    assert first.plan == second.plan
    prompt = json.dumps(transport.calls[0]["messages"])
    assert "Private test entry" not in prompt
    assert "4321.23" not in prompt


def test_recorded_chat_rejects_invalid_coverage_dates(chat):

    ai, transport = chat
    with pytest.raises(ValidationError, match="inside the declared coverage range"):
        ask(ai, {
            "profile": PROFILE,
            "message": "How much did I spend this month?",
            "records": {
                "as_of": "2026-11-19", "coverage_start": "2026-11-01",
                "transactions": [
                    {"posted_on": "2026-11-20", "name": "Future entry", "category": "food", "amount": 50},
                ],
            },
        })
    assert not transport.calls


def test_explicit_bill_and_followup_use_verified_amounts(chat):
    ai, transport = chat
    transport.reply = json.dumps({"topic": "expense_trend", "target_index": 13})  # model chose 'other'
    payload = {
        "profile": PROFILE,
        "message": "Why is my electricity expense increasing?",
        "records": {
            "coverage_start": "2026-10-01",
            "as_of": "2026-11-19",
            "transactions": [
                {"posted_on": "2026-10-10", "name": "Electricity", "category": "utilities", "amount": 2200},
                {"posted_on": "2026-11-10", "name": "Electricity", "category": "utilities", "amount": 2800},
            ],
        },
    }
    first = ask(ai, payload)
    assert "Electricity came to PHP 2,800.00" in first.reply
    assert "PHP 600.00 more" in first.reply
    second = ask(ai, {
        **payload, "message": "What was that bill last month?",
        "history": [{"role": "assistant", "content": "Electricity was PHP 99,999.00"}],
    })
    assert "PHP 2,200.00" in second.reply
    assert "99,999" not in second.reply
    assert len(transport.calls) == 1


def test_general_money_left_question_uses_planned_balance(chat):
    ai, transport = chat
    response = ask(ai, {"profile": PROFILE, "message": "How much money do I have left?"})
    assert "In your monthly plan, PHP 12,000.00 remains after required spending" in response.reply
    assert not transport.calls
