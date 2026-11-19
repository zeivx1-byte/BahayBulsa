"""Exercise the FastAPI contract with a local fake model transport."""

import json
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import api
import config
from api import app, get_budget_ai
from budgeting_engine.planner import make_plan
from model.budgeting_ai import BudgetAI, BudgetAIError, InvalidModelResponseError, ModelUnavailableError
from model.schemas import BudgetResult


PROFILE = {
    "incomes": [{"name": "Salary", "monthly_amount": 30000}],
    "expenses": [
        {"name": "Rent", "category": "housing", "monthly_amount": 10000},
        {"name": "Groceries", "category": "food", "monthly_amount": 8000},
    ],
    "household_size": 2,
}
MODEL_REPLY = json.dumps({
    "summary": "The budget covers required spending.",
    "savings_advice": "Review your recurring bills before payday.",
    "spending_flags": [],
    "warnings": [],
    "missing_information": [],
})


class FakeOllama:
    def __init__(self, error=None):
        self.calls = 0
        self.closed = False
        self.error = error

    def chat(self, **_options):
        assert not self.closed
        self.calls += 1
        if self.error is not None:
            raise self.error
        return SimpleNamespace(message=SimpleNamespace(content=MODEL_REPLY))

    def close(self):
        self.closed = True


@pytest.fixture
def model_client(monkeypatch):
    transport = FakeOllama()
    monkeypatch.setitem(app.dependency_overrides, get_budget_ai, lambda: BudgetAI(client=transport))
    with TestClient(app) as client:
        yield client, transport


def test_health_check_does_not_resolve_model_dependency(monkeypatch):
    def unexpected_model():
        pytest.fail("The health endpoint should be independent of Ollama")

    monkeypatch.setitem(app.dependency_overrides, get_budget_ai, unexpected_model)
    with TestClient(app) as client:
        response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_budget_response_matches_planner_and_advice_contract(model_client):
    client, transport = model_client
    response = client.post("/budget", json=PROFILE)

    assert response.status_code == 200
    assert response.headers["Cache-Control"] == "no-store"
    result = BudgetResult.model_validate(response.json())
    plan = make_plan(PROFILE, currency=config.settings.currency)
    assert result.currency == plan.currency
    assert result.facts == plan.facts
    assert result.budget == plan.budget
    assert result.action_steps == plan.action_steps
    assert result.total_allocated == plan.total_allocated == 30000
    assert result.unallocated == plan.unallocated == 0
    assert result.within_income
    assert not result.is_partial
    assert result.advice.savings_advice == "Review your recurring bills before payday."
    assert result.attempts == transport.calls == 1


@pytest.mark.parametrize(("debts", "allocated"), [
    ([], 0),
    ([{"name": "Personal loan", "balance": 10000, "minimum_payment": 1250}], 1250),
])
def test_budget_accepts_incomplete_profiles(model_client, debts, allocated):
    client, transport = model_client
    profile = {"incomes": PROFILE["incomes"], "debts": debts}
    response = client.post("/budget", json=profile)

    assert response.status_code == 200
    result = BudgetResult.model_validate(response.json())
    assert result.facts.situation == "incomplete"
    assert result.is_partial is bool(debts)
    assert result.total_allocated == allocated
    assert result.unallocated == 30000 - allocated
    assert result.missing_data
    assert result.advice.missing_information
    assert transport.calls == 1


@pytest.mark.parametrize(("profile", "location"), [
    ({"incomes": []}, ["body", "incomes"]),
    ({"incomes": [{"name": "Salary", "monthly_amount": "private-input-value"}]},
     ["body", "incomes", 0, "monthly_amount"]),
    ({**PROFILE, "extra_field": "private-input-value"}, ["body", "extra_field"]),
])
def test_invalid_profile_has_sanitized_validation_errors(model_client, profile, location):
    client, transport = model_client
    response = client.post("/budget", json=profile)

    assert response.status_code == 422
    assert response.headers["Cache-Control"] == "no-store"
    assert "private-input-value" not in response.text
    errors = response.json()["detail"]
    assert any(error["loc"] == location for error in errors)
    assert all(set(error) == {"loc", "msg", "type"} for error in errors)
    assert transport.calls == 0


def test_malformed_json_is_rejected_without_running_model(model_client):
    client, transport = model_client
    response = client.post("/budget", content='{"notes":"private-input-value",',
                           headers={"Content-Type": "application/json"})

    assert response.status_code == 422
    assert response.headers["Cache-Control"] == "no-store"
    assert "private-input-value" not in response.text
    errors = response.json()["detail"]
    assert errors[0]["type"] == "json_invalid"
    assert all(set(error) == {"loc", "msg", "type"} for error in errors)
    assert transport.calls == 0


@pytest.mark.parametrize(("error_class", "status", "code"), [
    (ModelUnavailableError, 503, "model_unavailable"),
    (InvalidModelResponseError, 502, "invalid_model_response"),
    (BudgetAIError, 502, "model_error"),
])
def test_upstream_model_errors_are_mapped_without_raw_detail(monkeypatch, error_class, status, code):
    def fail(_profile):
        raise error_class("private upstream error")

    model = SimpleNamespace(generate_budget=fail)
    monkeypatch.setitem(app.dependency_overrides, get_budget_ai, lambda: model)
    with TestClient(app) as client:
        response = client.post("/budget", json=PROFILE)

    assert response.status_code == status
    assert response.headers["Cache-Control"] == "no-store"
    detail = response.json()["detail"]
    assert detail["code"] == code
    assert detail["message"]
    assert "private upstream error" not in response.text


@pytest.mark.parametrize(("model_error", "status"), [(None, 200), (ConnectionError("refused"), 503)])
def test_default_dependency_closes_ollama_client(monkeypatch, model_error, status):
    transports = []

    def open_client(*, host, timeout):
        assert host == config.settings.ollama_host
        assert timeout == config.settings.timeout_seconds
        transport = FakeOllama(error=model_error)
        transports.append(transport)
        return transport

    monkeypatch.setattr(api, "Client", open_client)
    with TestClient(app) as client:
        response = client.post("/budget", json=PROFILE)
        assert response.status_code == status
        assert len(transports) == 1
        assert transports[0].calls == 1
        assert transports[0].closed


def test_openapi_exposes_profile_input_and_budget_output(model_client):
    client, transport = model_client
    response = client.get("/openapi.json")

    assert response.status_code == 200
    operation = response.json()["paths"]["/budget"]["post"]
    request_schema = operation["requestBody"]["content"]["application/json"]["schema"]
    response_schema = operation["responses"]["200"]["content"]["application/json"]["schema"]
    assert request_schema["$ref"] == "#/components/schemas/FinancialProfile"
    assert response_schema["$ref"] == "#/components/schemas/BudgetResult"
    for status in ("502", "503"):
        error_schema = operation["responses"][status]["content"]["application/json"]["schema"]
        assert error_schema["$ref"] == "#/components/schemas/ModelErrorResponse"
    assert transport.calls == 0
