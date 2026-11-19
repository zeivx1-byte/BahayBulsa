"""Verify desktop chat and planning against the existing JSON ledger."""

from datetime import date, timedelta
import json
from pathlib import Path

import pytest

from model.ai_service import ModelConfigurationError, ModelReply
from model.budgeting_ai import BudgetAI


@pytest.fixture
def desktop_api(monkeypatch, tmp_path):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2]))
    monkeypatch.chdir(tmp_path)
    from desktop_ai import DesktopBudgetOrganizerAPI

    return DesktopBudgetOrganizerAPI


def test_desktop_uses_posted_records_and_persists_follow_up_context(desktop_api):
    desktop = desktop_api()
    today = date.today()
    last_month = today.replace(day=1) - timedelta(days=1)
    desktop.add_transaction("Groceries", 750, "expense", today.isoformat(), "Private note")
    desktop.add_transaction("Groceries", 1_200, "expense", last_month.isoformat())

    first = desktop.ask_budget_ai("dashboard", "How much did I spend this month?")
    assert "750.00" in first["reply"]

    reopened = desktop_api()
    follow_up = reopened.ask_budget_ai("dashboard", "What about last month?")
    assert "1,200.00" in follow_up["reply"]
    assert len(reopened.get_chat_history()["histories"]["dashboard"]) == 4
    assert reopened.get_chat_history()["histories"]["demo"] == []
    assert reopened.reset_chat("dashboard") == {"history": []}
    assert desktop_api().get_chat_history()["histories"]["dashboard"] == []
    assert reopened.get_dashboard_data()["total_expense"] == 1_950


def test_desktop_plan_contains_verified_amounts_and_leaves_ledger_untouched(desktop_api):
    sent = []

    class Transport:
        def chat(self, **options):
            sent.append(options)
            content = json.dumps({
                "summary": "Make room for essentials first and set aside an emergency contribution.",
                "savings_advice": "Keep emergency savings accessible.",
                "spending_flags": [],
                "warnings": [],
                "missing_information": [],
            })
            return ModelReply(content)

    desktop = desktop_api(ai_factory=lambda: BudgetAI(client=Transport()))
    today = date.today().isoformat()
    desktop.add_transaction("Private income label", 30_000, "income", today)
    desktop.add_transaction("Groceries", 8_000, "expense", today, "Private payment note")
    ledger = Path("data/transactions.json").read_bytes()

    result = desktop.ask_budget_ai("dashboard", "Create a monthly budget plan.")
    plan = result["plan"]
    sections = plan["sections"]
    assert sections["income_overview"]["monthly_income"] == 30_000
    assert sections["expense_overview"]["monthly_estimate"] == 8_000
    assert sum(line["amount"] for line in sections["recommended_allocations"]) == plan["total_allocated"]
    assert Path("data/transactions.json").read_bytes() == ledger
    assert sent and "Private income label" not in str(sent) and "Private payment note" not in str(sent)
    assert "Planned savings" in desktop_api().get_chat_history()["histories"]["dashboard"][-1]["content"]


def test_desktop_provider_error_is_returned_without_saving_failed_turn(desktop_api):
    def unavailable():
        raise ModelConfigurationError("Set GROQ_API_KEY in the Python environment to use BudgetAI.")

    desktop = desktop_api(ai_factory=unavailable)

    assert "GROQ_API_KEY" in desktop.generate_budget_plan("demo")["error"]
    assert desktop.get_chat_history()["histories"]["demo"] == []
