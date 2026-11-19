"""Exercise the website's real SQLite ledger with separate fictional owners."""

from datetime import date
from pathlib import Path
import sqlite3

import pytest


@pytest.fixture
def ledger(monkeypatch, tmp_path):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2]))
    import website_store

    path = tmp_path / "website.sqlite3"
    return website_store, website_store.WebsiteStore(path), path


def _row(tx_id, day, category, amount, kind="expense"):
    return {"id": tx_id, "date": day, "category": category, "amount": amount, "type": kind, "note": ""}


def test_accounts_and_sessions_are_scoped_and_survive_restart(ledger, monkeypatch):
    module, store, path = ledger
    monkeypatch.setattr(module, "time", lambda: 1000)
    first = store.register("Ledger.One", "example-password-one")
    second = store.register("Ledger.Two", "example-password-two")
    with pytest.raises(module.UsernameUnavailable):
        store.register("  ledger.one  ", "other-example-password")
    assert store.login(first.username, "incorrect-password") is None
    assert store.login("unknown-account", "example-password-one") is None
    account, first_token = store.login("LEDGER.ONE", "example-password-one")
    assert account == first
    second_token = store.create_session(second.id)

    reopened = module.WebsiteStore(path)
    assert reopened.resolve_session(first_token) == first
    assert reopened.resolve_session(second_token) == second
    assert reopened.resolve_session("forged-session-token") is None
    with sqlite3.connect(path) as db:
        salt, digest = db.execute("SELECT salt, password_hash FROM accounts WHERE id = ?", (first.id,)).fetchone()
        (stored_session,) = db.execute("SELECT token_hash FROM sessions WHERE owner_id = ?", (first.id,)).fetchone()
        assert len(salt) == 16 and len(digest) == 32
        assert digest != b"example-password-one"
        assert stored_session != first_token.encode()
    reopened.revoke_session(first_token)
    assert store.resolve_session(first_token) is None
    assert store.resolve_session(second_token) == second
    monkeypatch.setattr(module, "time", lambda: 1000 + module._SESSION_SECONDS)
    assert reopened.resolve_session(second_token) is None


def test_dashboard_writes_are_atomic_and_retrieval_filters_owner_and_dates(ledger):
    module, store, path = ledger
    first = store.register("owner-one", "example-password-one")
    second = store.register("owner-two", "example-password-two")
    first_state = {
        "transactions": [
            _row(2, "2026-11-05", "Food", 10.125), _row(1, "2026-10-05", "Rent", 100),
            _row(3, "2026-12-05", "Future", 999),
        ],
        "upcoming_bills": [{"name": "Electricity"}],
        "next_transaction_id": 4,
    }
    expected = {**first_state, "transactions": [
        _row(2, "2026-11-05", "Food", 10.13), *first_state["transactions"][1:],
    ]}
    assert store.save_dashboard(first.id, first_state, 0) == 1
    store.save_dashboard(second.id, {"transactions": [_row(1, "2026-11-05", "Different owner", 500)]}, 0)
    reopened = module.WebsiteStore(path)
    assert reopened.load_dashboard(first.id) == (1, expected)
    assert reopened.load_organizer_state(first.id) == {key: value for key, value in first_state.items()
                                                       if key != "transactions"}
    start, end = date(2026, 11, 1), date(2026, 11, 30)
    assert reopened.query_transactions(first.id, start, end) == [expected["transactions"][0]]
    assert reopened.query_transactions(second.id, start, end)[0]["category"] == "Different owner"
    assert reopened.query_transactions(first.id, start, end, limit=0) == []
    previous_budget = {"monthly_budget": 500, "category_limits": {"food": 20}, "savings_goal": None}
    reopened.save_preferences(first.id, "2026-10", previous_budget)
    reopened.save_preferences(second.id, "2026-10", {**previous_budget, "monthly_budget": 600})
    rows, state, preferences, previous_preferences = reopened.load_chat_snapshot(first.id, start, end, "2026-11")
    assert rows == [expected["transactions"][0]]
    assert state == reopened.load_organizer_state(first.id)
    assert preferences == {"monthly_budget": None, "category_limits": {}, "savings_goal": None}
    assert previous_preferences == previous_budget

    with pytest.raises(module.RevisionConflict):
        store.save_dashboard(first.id, {"transactions": []}, 0)
    with pytest.raises(sqlite3.IntegrityError):
        repeated = first_state["transactions"][0]
        store.save_dashboard(first.id, {"transactions": [repeated, repeated]}, 1)
    assert store.load_dashboard(first.id) == (1, expected)


def test_preferences_and_conversation_revisions_are_isolated(ledger):
    module, store, path = ledger
    first = store.register("owner-one", "example-password-one")
    second = store.register("owner-two", "example-password-two")
    prefs = {"monthly_budget": 3000, "category_limits": {"food": 500},
             "savings_goal": {"name": "New phone", "target_amount": 1000, "current_amount": 25}}
    store.save_preferences(first.id, "2026-11", prefs)
    assert store.load_preferences(first.id, "2026-11") == prefs
    assert store.load_preferences(first.id, "2026-10")["monthly_budget"] is None
    assert store.load_preferences(second.id, "2026-11")["monthly_budget"] is None

    for index in range(6):
        store.append_conversation(first.id, index, f"Question {index}", f"Reply {index}", {"category": "food"})
    store.append_conversation(second.id, 0, "Another question", "Another reply", None)
    reopened = module.WebsiteStore(path)
    history = reopened.load_conversation(first.id)
    assert history.revision == 6
    assert history.history[0] == {"role": "user", "content": "Question 2"}
    assert len(history.history) == 8
    assert history.memory == {"category": "food"}
    cleared = reopened.reset_conversation(first.id)
    assert cleared.revision == 7 and cleared.history == [] and cleared.memory is None
    with pytest.raises(module.RevisionConflict):
        store.append_conversation(first.id, 6, "Stale question", "Stale reply", None)
    assert reopened.load_conversation(second.id).history[0]["content"] == "Another question"
