"""Local HTTP interface for the BudgetAI budgeting service."""

from collections.abc import Iterator
from contextlib import closing
from typing import Annotated, Literal

from fastapi import Body, Depends, FastAPI, HTTPException, Request, Response, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from ollama import Client
from pydantic import BaseModel

import config
from model.budget_chat import ScenarioError
from model.budgeting_ai import BudgetAI, BudgetAIError, InvalidModelResponseError, ModelUnavailableError
from model.schemas import BudgetResult, ChatRequest, ChatResponse, FinancialProfile


app = FastAPI(title="BudgetAI API", version="1.0.0")
PRIVATE_RESPONSE_HEADERS = {"Cache-Control": "no-store"}


class ModelErrorDetail(BaseModel):
    code: Literal["model_unavailable", "invalid_model_response", "model_error"]
    message: str


class ModelErrorResponse(BaseModel):
    detail: ModelErrorDetail


MODEL_ERROR_RESPONSES = {
    502: {"model": ModelErrorResponse, "description": "Ollama could not supply a usable explanation."},
    503: {"model": ModelErrorResponse, "description": "Ollama or the configured model is unavailable."},
}
CHAT_EXAMPLE_PROFILE = {
    "incomes": [{"name": "Salary", "monthly_amount": 30000}],
    "expenses": [
        {"name": "Rent", "category": "housing", "monthly_amount": 10000},
        {"name": "Groceries", "category": "food", "monthly_amount": 8000},
    ],
    "household_size": 2,
}
CHAT_EXAMPLES = {
    "question": {
        "summary": "Ask about a budget allocation",
        "value": {"profile": CHAT_EXAMPLE_PROFILE, "message": "Why did you put money in my emergency fund?"},
    },
    "scenario": {
        "summary": "Try a rent increase",
        "value": {"profile": CHAT_EXAMPLE_PROFILE, "message": "What if my rent goes up by ₱2,000?"},
    },
}


def get_budget_ai() -> Iterator[BudgetAI]:
    settings = config.settings
    with closing(Client(host=settings.ollama_host, timeout=settings.timeout_seconds)) as client:
        yield BudgetAI(settings=settings, client=client)


@app.exception_handler(RequestValidationError)
async def invalid_request(_request: Request, exc: RequestValidationError) -> JSONResponse:
    # The validation error can contain the original request data and exception context.
    detail = [{key: error[key] for key in ("loc", "msg", "type")} for error in exc.errors()]
    return JSONResponse(status_code=422, content={"detail": detail}, headers=PRIVATE_RESPONSE_HEADERS)


def _model_error(exc: BudgetAIError) -> HTTPException:
    if isinstance(exc, ModelUnavailableError):
        status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        code, message = "model_unavailable", "The local budget model is unavailable."
    elif isinstance(exc, InvalidModelResponseError):
        status_code = status.HTTP_502_BAD_GATEWAY
        code, message = "invalid_model_response", "The local budget model returned an invalid reply."
    else:
        status_code = status.HTTP_502_BAD_GATEWAY
        code, message = "model_error", "The local budget model could not complete the request."
    return HTTPException(status_code=status_code, detail={"code": code, "message": message},
                         headers=PRIVATE_RESPONSE_HEADERS)


@app.get("/health")
async def health() -> dict[str, str]:
    """Report API liveness independently of Ollama."""
    return {"status": "ok"}


@app.post("/budget", responses=MODEL_ERROR_RESPONSES)
def generate_budget(
    profile: FinancialProfile,
    response: Response,
    budget_ai: Annotated[BudgetAI, Depends(get_budget_ai)],
) -> BudgetResult:
    """Build a monthly budget with the Python planner and local model."""
    try:
        result = budget_ai.generate_budget(profile)
    except BudgetAIError as exc:
        raise _model_error(exc) from None

    response.headers.update(PRIVATE_RESPONSE_HEADERS)
    return result


@app.post("/chat", responses=MODEL_ERROR_RESPONSES)
def chat(
    request: Annotated[ChatRequest, Body(openapi_examples=CHAT_EXAMPLES)],
    response: Response,
    budget_ai: Annotated[BudgetAI, Depends(get_budget_ai)],
) -> ChatResponse:
    """Answer a question about a budget or compare a hypothetical expense change."""
    try:
        result = budget_ai.respond_to_chat(request)
    except ScenarioError as exc:
        raise RequestValidationError([{"loc": ("body", "change"), "msg": str(exc), "type": "value_error"}]) from None
    except BudgetAIError as exc:
        raise _model_error(exc) from None
    response.headers.update(PRIVATE_RESPONSE_HEADERS)
    return result
