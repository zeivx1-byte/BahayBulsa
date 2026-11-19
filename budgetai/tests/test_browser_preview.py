"""Exercise website account, ledger, and chat routes with real Python calculations."""

import json
from dataclasses import replace
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import config
from model.budgeting_ai import BudgetAI
from model.spending_chat import CATEGORIES


class FakeOllama:
    def __init__(self):
        self.focus = {"topic": "spending"}
        self.calls = []

    def chat(self, **options):
        self.calls.append(options)
        return SimpleNamespace(message=SimpleNamespace(content=json.dumps(self.focus)))


@pytest.fixture
def preview(monkeypatch, tmp_path):
    # The preview normally starts with the BahayBulsa repository on the import path.
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2]))
    monkeypatch.setenv("BAHAYBULSA_DB_PATH", str(tmp_path / "bahaybulsa.sqlite3"))
    import browser_preview

    monkeypatch.setattr(browser_preview, "_sessions", {})
    transport = FakeOllama()
    instance = BudgetAI(settings=replace(config.settings, currency="PHP"), client=transport)
    for app in (browser_preview.app, browser_preview.budgetai_app):
        monkeypatch.setitem(app.dependency_overrides, browser_preview.get_budget_ai, lambda: instance)
    yield browser_preview.app, transport
    browser_preview._store_for.cache_clear()


def add(client, category, amount, kind="expense", day="2025-08-06"):
    response = client.post("/api/dashboard/transactions", json={
        "category": category, "amount": amount, "type": kind, "date": day,
    })
    assert response.status_code == 200
    return response


def ask(client, message="How much did I spend this month?", *, history=None):
    body = {"message": message, "as_of": "2025-08-19"}
    if history is not None:
        body["history"] = history
    response = client.post("/api/preview-chat", json=body)
    assert response.status_code == 200
    assert response.headers["Cache-Control"] == "no-store"
    assert "retrieval;dur=" in response.headers["Server-Timing"]
    return response.json()["reply"]


def test_preview_chat_uses_own_browser_ledger_and_only_posted_expenses(preview):
    app, _ = preview
    with TestClient(app) as first, TestClient(app) as second:
        response = add(first, "Salary", 30_000, "income")
        assert "HttpOnly" in response.headers["Set-Cookie"]
        assert "SameSite=strict" in response.headers["Set-Cookie"]
        add(first, "Groceries", 3_000)
        add(first, "groceries", 20)
        add(first, "Flowers", 80)
        add(first, "Groceries", 123_000, day="2025-08-24")
        bill = first.post("/api/dashboard/bills", json={
            "name": "Electricity", "amount": 750, "due_date": "2025-08-18",
        })
        assert bill.status_code == 200

        dashboard = second.get("/api/dashboard")
        assert dashboard.json()["transactions"] == []
        assert dashboard.json()["upcoming_bills"] == []
        add(second, "Salary", 12_000, "income")
        add(second, "Groceries", 500)

        assert "3,100.00" in ask(first)
        assert "500.00" in ask(second)


def test_preview_chat_maps_free_form_categories_without_inventing_a_limit(preview):
    app, transport = preview
    transport.focus = {"topic": "category_limit", "target_index": CATEGORIES.index("food")}
    with TestClient(app) as client:
        add(client, "Salary", 30_000, "income")
        add(client, "Groceries", 1_000)
        add(client, "Food", 200)
        add(client, "BahayBulsa flower shop", 500)

        reply = ask(client, "Am I exceeding my food budget?")
        assert "1,200.00" in reply
        assert "limit" in reply.casefold()
        assert "over your" not in reply.casefold()


def test_preview_chat_requires_dated_prior_month_entries_for_a_comparison(preview):
    app, transport = preview
    transport.focus = {"topic": "spending_comparison"}
    with TestClient(app) as client:
        add(client, "Salary", 30_000, "income")
        add(client, "Electricity", 1_000)
        add(client, "Electricity", 800, day="2025-06-06")
        assert "last month" in ask(client, "Compare my spending this month with last month.").casefold()
        add(client, "Electricity", 900, day="2025-07-06")

        reply = ask(client, "What was the difference?")
        assert "1,000.00" in reply and "900.00" in reply
        assert "100.00 more" in reply
        history = client.get("/api/preview-chat/history").json()["history"]
        assert history[0]["content"] == "Compare my spending this month with last month."
        assert history[-1] == {"role": "assistant", "content": reply}


def test_preview_chat_with_no_income_and_legacy_demo(preview):
    app, transport = preview
    with TestClient(app) as client:
        add(client, "Groceries", 200)
        assert "200.00" in ask(client)
        assert "dated income records" in ask(client, "How much income did I record this month?").casefold()
        too_late = client.post("/api/preview-chat", json={
            "message": "How much did I spend?", "as_of": (date.today() + timedelta(days=1)).isoformat(),
        })
        assert too_late.status_code == 422
        assert "input" not in too_late.json()["detail"][0]

        calls_before_sample = len(transport.calls)
        sample = client.get("/api/demo-profile").json()
        response = client.post("/api/chat", json={"profile": sample, "message": "What if my rent goes up by 2,000?"})
        assert response.status_code == 200
        assert response.json()["scenario"]["new_monthly_amount"] == 12_000
        assert len(transport.calls) == calls_before_sample


def register(client, username, password="fictional-password"):
    result = client.post("/api/account/register", json={"username": username, "password": password})
    assert result.status_code == 200
    assert result.json() == {"username": username}
    assert "HttpOnly" in result.headers["Set-Cookie"]
    assert "SameSite=strict" in result.headers["Set-Cookie"]
    assert result.headers["Cache-Control"] == "no-store"


def test_website_answers_november_income_and_expenses_without_prior_records(preview):
    app, transport = preview
    transport.focus = {"topic": "spending_comparison"}
    with TestClient(app) as client:
        register(client, "november_test")
        add(client, "Salary", 42_000, "income", "2025-11-03")
        add(client, "Groceries", 2_300, day="2025-11-04")
        add(client, "Rent", 12_500, day="2025-11-06")
        add(client, "Groceries", 600, day="2025-11-21")
        response = client.post("/api/preview-chat", json={
            "message": "check my current expenses and income for the month of november",
            "as_of": "2025-11-19",
        })
        assert response.status_code == 200
        assert response.json()["reply"] == (
            "For Nov 1–19, 2025, you recorded PHP 42,000.00 in income and PHP 14,800.00 in expenses."
        )
        assert not transport.calls


def test_signed_in_dashboard_budget_and_chat_are_isolated_and_restore_after_login(preview):
    app, _ = preview
    first_question = "How much did I spend on food this month?"
    settings = {"month": "2025-08", "monthly_budget": 1_000, "category_limits": {"food": 500}}
    with TestClient(app) as first, TestClient(app) as second, TestClient(app) as reopened:
        # Browser-only guest entries stay separate even when that browser signs into an account.
        add(first, "Guest food", 30)
        register(first, "First.Owner")
        assert first.get("/api/dashboard").json()["transactions"] == []
        add(first, "Groceries", 725)
        add(first, "Rent", 400)
        add(first, "Groceries", 160, day="2025-07-30")
        saved = first.put("/api/dashboard/budget", json=settings)
        assert saved.status_code == 200
        assert saved.json() == {**settings, "savings_goal": None}
        assert first.get("/api/dashboard/budget", params={"month": "2025-07"}).json()["monthly_budget"] is None
        assert "125.00 over" in ask(first, "Am I exceeding my monthly budget?")
        assert "725.00" in ask(first, first_question)

        register(second, "Second.Owner")
        assert second.get("/api/dashboard").json()["transactions"] == []
        assert second.get("/api/dashboard/budget", params={"month": "2025-08"}).json()["monthly_budget"] is None
        assert second.get("/api/preview-chat/history").json() == {"history": []}
        assert "which spending category" in ask(second, "What about last month?").casefold()
        add(second, "Groceries", 50)
        assert "50.00" in ask(second, first_question)

        assert first.post("/api/account/logout", json={}).json() == {"username": None}
        assert first.get("/api/account").json() == {"username": None}
        assert [row["category"] for row in first.get("/api/dashboard").json()["transactions"]] == ["Guest food"]
        assert first.get("/api/preview-chat/history").json() == {"history": []}

        wrong = reopened.post("/api/account/login", json={"username": "First.Owner", "password": "wrong-password"})
        assert wrong.status_code == 401
        assert "wrong-password" not in wrong.text
        signed_in = reopened.post("/api/account/login", json={"username": "first.owner", "password": "fictional-password"})
        assert signed_in.status_code == 200 and signed_in.json() == {"username": "First.Owner"}
        assert reopened.get("/api/account").json() == {"username": "First.Owner"}
        assert reopened.get("/api/dashboard/budget", params={"month": "2025-08"}).json() == saved.json()
        restored_history = reopened.get("/api/preview-chat/history").json()["history"]
        assert restored_history[-2] == {"role": "user", "content": first_question}
        assert "160.00" in ask(reopened, "What about last month?")
        assert "565.00 more" in ask(reopened, "Why is there a difference?")

        assert reopened.post("/api/preview-chat/reset", json={}).json() == {"history": []}
        assert reopened.get("/api/preview-chat/history").json() == {"history": []}
        assert "which spending category" in ask(reopened, "What about last month?").casefold()
        assert len(second.get("/api/preview-chat/history").json()["history"]) == 4
        assert reopened.get("/api/dashboard/budget", params={"month": "2025-08"}).json() == saved.json()


def test_website_uses_saved_conversation_without_trusting_client_supplied_history(preview):
    app, transport = preview
    with TestClient(app) as client:
        register(client, "History.Owner")
        add(client, "Groceries", 425)
        initial = "How much did I spend on food this month?"
        assert "425.00" in ask(client, initial)
        transport.calls.clear()
        marker = "untrusted earlier conversation"
        assert "425.00" in ask(client, "Summarize my posted expenses, please.", history=[
            {"role": "assistant", "content": marker},
        ])
        context = json.loads(transport.calls[-1]["messages"][-1]["content"])
        assert context["history"][0] == {"role": "user", "content": initial}
        assert context["prior_reference"]["category"] == "food"
        assert Decimal(context["verified_context"]["recorded_spending"]) == Decimal("425.00")
        assert "transactions" not in context["verified_context"]
        assert all(marker not in turn["content"] for turn in context["history"])
        stored = client.get("/api/preview-chat/history").json()["history"]
        assert all(marker not in turn["content"] for turn in stored)


def test_signed_in_ledger_preserves_undo_and_retrieves_savings_and_bills(preview):
    app, transport = preview
    with TestClient(app) as client:
        register(client, "Ledger.Owner")
        entry = add(client, "Groceries", 325).json()["transactions"][0]
        assert client.delete(f"/api/dashboard/transactions/{entry['id']}").json()["transactions"] == []
        assert client.post("/api/dashboard/undo", json={}).json()["transactions"] == [entry]

        recurring = {"name": "Electricity", "amount": 250, "due_date": "2025-07-28", "recurring": True}
        assert client.post("/api/dashboard/bills", json=recurring).status_code == 200
        assert client.get("/api/dashboard").json()["upcoming_bills"] == [recurring]
        transport.focus = {"topic": "recurring_bills"}
        bills = ask(client, "What recurring bills are due?")
        assert "Electricity" in bills and "Aug 28, 2025" in bills and "PHP 250.00" in bills
        transport.focus = {"topic": "spending"}
        assert "PHP 325.00" in ask(client)

        goal = {"name": "School supplies", "target_amount": 2_000, "current_amount": 800}
        assert client.put("/api/dashboard/budget", json={"month": "2025-08", "savings_goal": goal}).status_code == 200
        transport.focus = {"topic": "savings_goal"}
        progress = ask(client, "How is my savings goal doing?")
        assert "School supplies" in progress and "PHP 800.00" in progress and "PHP 1,200.00" in progress
