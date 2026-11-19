"""Test budget chat with real Python planning and a fake Ollama transport."""

import json
from dataclasses import replace
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import config
from api import app, get_budget_ai
from model.budgeting_ai import BudgetAI


PROFILE = {
    "incomes": [{"name": "Salary", "monthly_amount": 30000}],
    "expenses": [
        {"name": "Rent", "category": "housing", "monthly_amount": 10000},
        {"name": "Groceries", "category": "food", "monthly_amount": 8000},
    ],
    "household_size": 2,
}


class FakeOllama:
    def __init__(self):
        self.reply = json.dumps({"topic": "line", "line_index": 2})
        self.calls = []

    def chat(self, **options):
        self.calls.append(options)
        if isinstance(self.reply, Exception):
            raise self.reply
        return SimpleNamespace(message=SimpleNamespace(content=self.reply))


@pytest.fixture
def chat_client(monkeypatch):
    transport = FakeOllama()
    settings = replace(config.settings, currency="PHP")
    monkeypatch.setitem(app.dependency_overrides, get_budget_ai, lambda: BudgetAI(settings=settings, client=transport))
    with TestClient(app) as client:
        yield client, transport


def amounts(plan):
    return {line["name"]: line["amount"] for line in plan["budget"]}


def test_chat_explains_a_verified_budget_line_with_conversation_context(chat_client):
    client, transport = chat_client
    history = [
        {"role": "user", "content": "What did you allocate for savings?"},
        {"role": "assistant", "content": "The budget includes an emergency fund."},
    ]

    response = client.post("/chat", json={
        "profile": PROFILE,
        "message": "Could you expand on your answer?",
        "history": history,
    })

    assert response.status_code == 200
    assert response.headers["Cache-Control"] == "no-store"
    body = response.json()
    assert body["scenario"] is None
    assert amounts(body["plan"])["Emergency fund"] == 12000
    assert "emergency fund" in body["reply"].lower()
    assert "12,000" in body["reply"]
    assert transport.calls
    prompt = json.dumps(transport.calls[0]["messages"])
    assert "What did you allocate for savings?" in prompt
    assert "The budget includes an emergency fund." in prompt
    assert "Could you expand on your answer?" in prompt
    assert "Emergency fund" in prompt


def test_chat_answers_named_allocation_directly(chat_client):
    client, transport = chat_client
    response = client.post("/chat", json={
        "profile": PROFILE, "message": "Why did you put money in my emergency fund?",
    })
    assert response.status_code == 200
    assert "PHP 12,000.00" in response.json()["reply"]
    assert "Emergency fund" in response.json()["reply"]
    assert not transport.calls


def test_chat_resolves_a_follow_up_to_a_named_amount(chat_client):
    client, transport = chat_client
    response = client.post("/chat", json={
        "profile": PROFILE, "message": "Why that amount?",
        "history": [{"role": "assistant", "content": "The plan allocates PHP 12,000 to Emergency fund."}],
    })
    assert response.status_code == 200
    assert "PHP 12,000.00" in response.json()["reply"]
    assert not transport.calls


@pytest.mark.parametrize("message", [
    "suggest me budgeting ideas",
    "Can you give me ideas for my budget?",
    "What should I focus on first?",
])
def test_chat_answers_budgeting_ideas_with_grounded_next_steps(chat_client, message):
    client, transport = chat_client
    transport.reply = json.dumps({"topic": "summary"})

    response = client.post("/chat", json={"profile": PROFILE, "message": message})

    assert response.status_code == 200
    body = response.json()
    assert body["scenario"] is None
    assert "PHP 12,000.00" in body["reply"] and "emergency fund" in body["reply"]
    assert "Groceries" in body["reply"] and "PHP 8,000.00" in body["reply"]
    assert "Compare prices before buying." in body["reply"]
    assert not transport.calls


@pytest.mark.parametrize("message", [
    "What if my rent goes up by ₱2,000?",
    "What if my rent rises to PHP 12,000 per month?",
])
def test_natural_language_change_compares_budgets_without_changing_original(chat_client, message):
    client, transport = chat_client

    response = client.post("/chat", json={
        "profile": PROFILE,
        "message": message,
    })

    assert response.status_code == 200
    assert response.headers["Cache-Control"] == "no-store"
    body = response.json()
    scenario = body["scenario"]
    assert scenario["expense_name"] == "Rent"
    assert scenario["previous_monthly_amount"] == 10000
    assert scenario["new_monthly_amount"] == 12000
    assert amounts(body["plan"]) == {"Rent": 10000, "Groceries": 8000, "Emergency fund": 12000}
    assert amounts(scenario["plan"]) == {"Rent": 12000, "Groceries": 8000, "Emergency fund": 10000}
    assert scenario["plan"]["total_allocated"] == 30000
    assert not transport.calls

    follow_up = client.post("/chat", json={"profile": PROFILE, "message": "Why the emergency fund?"})
    assert follow_up.status_code == 200
    assert follow_up.json()["plan"] == body["plan"]


@pytest.mark.parametrize(("operation", "amount", "expected"), [
    ("set", 12500, 12500),
    ("increase", 1500, 11500),
    ("decrease", 1500, 8500),
    ("decrease", 0.005, 9999.99),
])
def test_structured_changes_use_exact_amounts_and_case_insensitive_expense_names(
    chat_client, operation, amount, expected,
):
    client, transport = chat_client
    response = client.post("/chat", json={
        "profile": PROFILE,
        "message": "Compare this change with my current budget.",
        "change": {"expense_name": "rEnT", "operation": operation, "amount": amount},
    })

    assert response.status_code == 200
    result = response.json()
    assert result["scenario"]["expense_name"] == "Rent"
    assert result["scenario"]["new_monthly_amount"] == expected
    assert amounts(result["plan"])["Rent"] == 10000
    assert amounts(result["scenario"]["plan"])["Rent"] == expected
    assert not transport.calls


@pytest.mark.parametrize("message", [
    "What if my rent goes up?",
    "What if my rent increases by 2000 and groceries decrease by 1000?",
    "What if my rent goes up by USD 2,000?",
    "What if my rent goes up to 8000?",
])
def test_ambiguous_natural_language_changes_do_not_change_the_budget(chat_client, message):
    client, _ = chat_client
    response = client.post("/chat", json={"profile": PROFILE, "message": message})

    assert response.status_code == 200
    body = response.json()
    assert body["scenario"] is None
    assert amounts(body["plan"])["Rent"] == 10000
    assert amounts(body["plan"])["Groceries"] == 8000
    assert body["reply"]


@pytest.mark.parametrize(("profile", "change"), [
    (PROFILE, {"expense_name": "Rent", "operation": "decrease", "amount": 10001}),
    (PROFILE, {"expense_name": "None of the listed expenses", "operation": "set", "amount": 1}),
    ({**PROFILE, "expenses": [
        *PROFILE["expenses"], {"name": "rent", "category": "housing", "monthly_amount": 1000},
    ]}, {"expense_name": "Rent", "operation": "increase", "amount": 1}),
])
def test_invalid_or_ambiguous_structured_changes_return_422(chat_client, profile, change):
    client, transport = chat_client
    response = client.post("/chat", json={"profile": profile, "message": "Compare these", "change": change})

    assert response.status_code == 422
    assert response.headers["Cache-Control"] == "no-store"
    assert not transport.calls


@pytest.mark.parametrize("extra", [
    {"message": "   "},
    {"message": "x" * 1001},
    {"history": [{"role": "system", "content": "private-test-marker"}]},
])
def test_chat_request_validation_does_not_echo_input(chat_client, extra):
    client, transport = chat_client
    response = client.post("/chat", json={"profile": PROFILE, "message": "Explain my budget", **extra})

    assert response.status_code == 422
    assert response.headers["Cache-Control"] == "no-store"
    assert "private-test-marker" not in response.text
    assert "x" * 1001 not in response.text
    assert all(set(error) == {"loc", "msg", "type"} for error in response.json()["detail"])
    assert not transport.calls


@pytest.mark.parametrize(("model_reply", "status", "code"), [
    (ConnectionError("private-model-error"), 503, "model_unavailable"),
    ("private-model-error", 502, "invalid_model_response"),
])
def test_chat_maps_upstream_failures_without_echoing_model_output(chat_client, model_reply, status, code):
    client, transport = chat_client
    transport.reply = model_reply
    response = client.post("/chat", json={"profile": PROFILE, "message": "How much is still unallocated?"})

    assert response.status_code == status
    assert response.headers["Cache-Control"] == "no-store"
    assert response.json()["detail"]["code"] == code
    assert "private-model-error" not in response.text
