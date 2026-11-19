"""Serve BahayBulsa's dashboard, local accounts, and retrieval-backed chat."""

from copy import deepcopy
from dataclasses import dataclass, field
from datetime import date as CalendarDate
from functools import lru_cache
from os import getenv
from pathlib import Path
from secrets import token_urlsafe
from threading import RLock
from time import perf_counter
from typing import Annotated, Callable, Literal

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import AfterValidator, BaseModel, ConfigDict, Field, StringConstraints, ValidationError, field_validator

from api import CHAT_EXAMPLE_PROFILE, PRIVATE_RESPONSE_HEADERS, app as budgetai_app
from api import _model_error, get_budget_ai, invalid_request
from main import BudgetOrganizerAPI
from model.budgeting_ai import BudgetAI, BudgetAIError
from model.schemas import ChatTurn, ExpenseCategory, Money, PositiveMoney, SavingsGoal
from website_context import build_ledger_request, prior_month_start
from website_store import Account, Conversation, RevisionConflict, UsernameUnavailable, WebsiteStore


app = FastAPI(title="BahayBulsa website", docs_url=None, redoc_url=None, openapi_url=None)
app.add_exception_handler(RequestValidationError, invalid_request)
SESSION_COOKIE = "bahaybulsa_preview"
ACCOUNT_COOKIE = "bahaybulsa_account"
_DEFAULT_DB = Path(__file__).resolve().parent / "results" / "bahaybulsa-web.sqlite3"
_GUEST_LOCK = RLock()


@dataclass
class GuestLedger:
    organizer: BudgetOrganizerAPI = field(default_factory=lambda: BudgetOrganizerAPI(persist=False))
    preferences: dict[str, dict] = field(default_factory=dict)
    conversation: Conversation = field(default_factory=lambda: Conversation(0, [], None))
    lock: RLock = field(default_factory=RLock)


_sessions: dict[str, GuestLedger] = {}


@lru_cache(maxsize=4)
def _store_for(path: Path) -> WebsiteStore:
    return WebsiteStore(path)


def _store() -> WebsiteStore:
    return _store_for(Path(getenv("BAHAYBULSA_DB_PATH") or _DEFAULT_DB).resolve())


def _set_cookie(response: Response, request: Request, name: str, value: str) -> None:
    response.set_cookie(name, value, httponly=True, samesite="strict", secure=request.url.scheme == "https")


def _account(request: Request) -> Account | None:
    token = request.cookies.get(ACCOUNT_COOKIE)
    return _store().resolve_session(token) if token else None


def _scope(request: Request, response: Response) -> Account | GuestLedger:
    response.headers.update(PRIVATE_RESPONSE_HEADERS)
    account = _account(request)
    if account is not None:
        return account
    if request.cookies.get(ACCOUNT_COOKIE):
        raise HTTPException(401, "Your account session expired. Sign in again to open your dashboard.",
                            headers=PRIVATE_RESPONSE_HEADERS)
    with _GUEST_LOCK:
        session_id = request.cookies.get(SESSION_COOKIE)
        guest = _sessions.get(session_id) if session_id else None
        if guest is None:
            session_id = token_urlsafe(32)
            guest = GuestLedger()
            _sessions[session_id] = guest
            _set_cookie(response, request, SESSION_COOKIE, session_id)
        return guest


LedgerScope = Annotated[Account | GuestLedger, Depends(_scope)]

EntryName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)]


class Transaction(BaseModel):
    category: EntryName
    amount: PositiveMoney
    type: Literal["income", "expense"]
    date: CalendarDate


class Bill(BaseModel):
    name: EntryName
    amount: PositiveMoney
    due_date: CalendarDate
    recurring: bool = False


class SortRequest(BaseModel):
    algorithm: Literal["merge", "insertion"]


class BenchmarkRequest(BaseModel):
    dataset_size: Literal[100, 1000, 5000]


class UndoRequest(BaseModel):
    pass


class PreviewQuestion(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    message: str = Field(min_length=1, max_length=1000)
    history: list[ChatTurn] = Field(default_factory=list, max_length=8)
    as_of: CalendarDate = Field(default_factory=CalendarDate.today)

    @field_validator("as_of")
    @classmethod
    def ensure_posted_date(cls, value: CalendarDate) -> CalendarDate:
        if value > CalendarDate.today():
            raise ValueError("Choose a reporting date that is today or earlier.")
        return value


class PreviewReply(BaseModel):
    reply: str


class AccountCredentials(BaseModel):
    model_config = ConfigDict(extra="forbid")
    username: str = Field(min_length=3, max_length=32, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]+$")
    password: str = Field(min_length=8, max_length=128)


def _valid_month(month: str) -> str:
    CalendarDate.fromisoformat(f"{month}-01")
    return month


Month = Annotated[str, StringConstraints(pattern=r"^[0-9]{4}-[0-9]{2}$"), AfterValidator(_valid_month)]


class BudgetPreferences(BaseModel):
    model_config = ConfigDict(extra="forbid")
    month: Month
    monthly_budget: Money | None = None
    category_limits: dict[ExpenseCategory, Money] = Field(default_factory=dict)
    savings_goal: SavingsGoal | None = None


def _account_response(request: Request, response: Response, account: Account, token: str) -> dict:
    old_token = request.cookies.get(ACCOUNT_COOKIE)
    if old_token:
        _store().revoke_session(old_token)
    _set_cookie(response, request, ACCOUNT_COOKIE, token)
    response.headers.update(PRIVATE_RESPONSE_HEADERS)
    return {"username": account.username}


def _restore_dashboard(state: dict) -> BudgetOrganizerAPI:
    organizer = BudgetOrganizerAPI(persist=False)
    organizer.transactions = state.get("transactions", [])
    organizer.savings_goal = state.get("savings_goal", organizer.savings_goal)
    organizer._next_transaction_id = state.get("next_id", 1)
    organizer._rebuild_structures()
    for item in state.get("undo", []):
        organizer.undo_stack.push(item)
    for bill in state.get("bills", []):
        organizer.bill_queue.insert(bill)
    return organizer


def _dashboard_state(organizer: BudgetOrganizerAPI) -> dict:
    return {"transactions": organizer.transactions, "savings_goal": organizer.savings_goal,
            "next_id": organizer._next_transaction_id, "undo": organizer.undo_stack._items,
            "bills": organizer.bill_queue.get_sorted_bills()}


def _conflict() -> HTTPException:
    return HTTPException(409, "Another request updated this session. Refresh or try again.",
                         headers=PRIVATE_RESPONSE_HEADERS)


def _edit_dashboard(scope: Account | GuestLedger, change: Callable[[BudgetOrganizerAPI], dict]) -> dict:
    if isinstance(scope, GuestLedger):
        with scope.lock:
            return deepcopy(change(scope.organizer))
    store = _store()
    revision, state = store.load_dashboard(scope.id)
    organizer = _restore_dashboard(state)
    result = change(organizer)
    try:
        store.save_dashboard(scope.id, _dashboard_state(organizer), revision)
    except RevisionConflict:
        raise _conflict() from None
    return result


def _conversation(scope: Account | GuestLedger) -> Conversation:
    if isinstance(scope, GuestLedger):
        with scope.lock:
            return scope.conversation
    return _store().load_conversation(scope.id)


def _append_conversation(scope: Account | GuestLedger, revision: int, message: str, reply: str,
                         memory: dict | None) -> None:
    if isinstance(scope, GuestLedger):
        with scope.lock:
            current = scope.conversation
            if current.revision != revision:
                raise _conflict()
            pair = [{"role": "user", "content": message}, {"role": "assistant", "content": reply[:1000]}]
            scope.conversation = Conversation(revision + 1, (current.history + pair)[-8:], memory)
        return
    try:
        _store().append_conversation(scope.id, revision, message, reply, memory)
    except RevisionConflict:
        raise _conflict() from None


@app.get("/")
async def index():
    return RedirectResponse("/preview/")


@app.get("/api/account")
def current_account(request: Request, response: Response):
    response.headers.update(PRIVATE_RESPONSE_HEADERS)
    account = _account(request)
    return {"username": account.username if account else None}


@app.post("/api/account/register")
def register(credentials: AccountCredentials, request: Request, response: Response):
    store = _store()
    try:
        account = store.register(credentials.username, credentials.password)
    except UsernameUnavailable:
        raise HTTPException(409, "That username is unavailable.", headers=PRIVATE_RESPONSE_HEADERS) from None
    return _account_response(request, response, account, store.create_session(account.id))


@app.post("/api/account/login")
def login(credentials: AccountCredentials, request: Request, response: Response):
    session = _store().login(credentials.username, credentials.password)
    if session is None:
        raise HTTPException(401, "Username or password is incorrect.", headers=PRIVATE_RESPONSE_HEADERS)
    return _account_response(request, response, *session)


@app.post("/api/account/logout")
def logout(request: Request, response: Response):
    token = request.cookies.get(ACCOUNT_COOKIE)
    if token:
        _store().revoke_session(token)
    response.delete_cookie(ACCOUNT_COOKIE)
    response.headers.update(PRIVATE_RESPONSE_HEADERS)
    return {"username": None}


@app.get("/api/dashboard")
def dashboard(scope: LedgerScope):
    if isinstance(scope, GuestLedger):
        with scope.lock:
            return deepcopy(scope.organizer.get_dashboard_data())
    _, state = _store().load_dashboard(scope.id)
    return _restore_dashboard(state).get_dashboard_data()


@app.post("/api/dashboard/transactions")
def add_transaction(transaction: Transaction, scope: LedgerScope):
    return _edit_dashboard(scope, lambda organizer: organizer.add_transaction(
        transaction.category, transaction.amount, transaction.type, transaction.date.isoformat()
    ))


@app.delete("/api/dashboard/transactions/{transaction_id}")
def delete_transaction(transaction_id: int, scope: LedgerScope):
    return _edit_dashboard(scope, lambda organizer: organizer.delete_transaction(transaction_id))


@app.post("/api/dashboard/undo")
def undo(_request: UndoRequest, scope: LedgerScope):
    return _edit_dashboard(scope, BudgetOrganizerAPI.undo_last_action)


@app.post("/api/dashboard/bills")
def add_bill(bill: Bill, scope: LedgerScope):
    def add(organizer: BudgetOrganizerAPI) -> dict:
        organizer.bill_queue.insert(bill.model_dump(mode="json"))
        return organizer.get_dashboard_data()

    return _edit_dashboard(scope, add)


@app.post("/api/dashboard/sort")
def sort_transactions(request: SortRequest, scope: LedgerScope):
    return _edit_dashboard(scope, lambda organizer: organizer.sort_transactions(request.algorithm))


@app.post("/api/dashboard/benchmark")
def benchmark(request: BenchmarkRequest, _scope: LedgerScope):
    return BudgetOrganizerAPI(persist=False).benchmark_runtime(request.dataset_size)


@app.get("/api/dashboard/budget")
def budget_settings(scope: LedgerScope, month: Month | None = None) -> BudgetPreferences:
    month = month or CalendarDate.today().isoformat()[:7]
    if isinstance(scope, GuestLedger):
        with scope.lock:
            values = scope.preferences.get(month, {})
    else:
        values = _store().load_preferences(scope.id, month)
    return BudgetPreferences(month=month, **values)


@app.put("/api/dashboard/budget")
def save_budget(budget: BudgetPreferences, scope: LedgerScope) -> BudgetPreferences:
    values = budget.model_dump(mode="json", exclude={"month"})
    if isinstance(scope, GuestLedger):
        with scope.lock:
            scope.preferences[budget.month] = values
    else:
        _store().save_preferences(scope.id, budget.month, values)
    return budget


@app.get("/api/demo-profile")
async def demo_profile():
    return CHAT_EXAMPLE_PROFILE


@app.get("/api/preview-chat/history")
def preview_history(scope: LedgerScope):
    return {"history": _conversation(scope).history}


@app.post("/api/preview-chat/reset")
def reset_chat(scope: LedgerScope):
    if isinstance(scope, GuestLedger):
        with scope.lock:
            scope.conversation = Conversation(scope.conversation.revision + 1, [], None)
    else:
        _store().reset_conversation(scope.id)
    return {"history": []}


@app.post("/api/preview-chat")
def preview_chat(
    question: PreviewQuestion,
    scope: LedgerScope,
    response: Response,
    budget_ai: Annotated[BudgetAI, Depends(get_budget_ai)],
) -> PreviewReply:
    start = perf_counter()
    conversation = _conversation(scope)
    first_day = prior_month_start(question.as_of.replace(day=1))
    month = question.as_of.isoformat()[:7]
    if isinstance(scope, GuestLedger):
        with scope.lock:
            rows = [row for row in scope.organizer.transactions
                    if first_day.isoformat() <= row["date"] <= question.as_of.isoformat()]
            bills = scope.organizer.bill_queue.get_sorted_bills()
            preferences = scope.preferences.get(month, {})
            previous_month = first_day.isoformat()[:7]
            previous_preferences = scope.preferences.get(previous_month, {}) if previous_month != month else {}
    else:
        rows, state, preferences, previous_preferences = _store().load_chat_snapshot(
            scope.id, first_day, question.as_of, month,
        )
        bills = state.get("bills", [])
    if len(rows) > 5000:
        raise HTTPException(422, "There are too many ledger entries in this reporting period.",
                            headers=PRIVATE_RESPONSE_HEADERS)
    try:
        # Legacy clients may send history. The website only uses history saved for this owner.
        request = build_ledger_request(question.message, question.as_of, rows, bills, preferences, previous_preferences,
                                       conversation.history, conversation.memory)
    except ValidationError:
        raise HTTPException(
            status_code=422, detail="These dashboard entries exceed BudgetAI’s supported amounts or record limits.",
            headers=PRIVATE_RESPONSE_HEADERS,
        ) from None
    answer_start = perf_counter()
    try:
        result = budget_ai.respond_to_ledger_chat(request)
    except BudgetAIError as exc:
        raise _model_error(exc) from None
    end = perf_counter()
    memory = result.memory.model_dump(mode="json") if result.memory else None
    _append_conversation(scope, conversation.revision, question.message, result.reply, memory)
    response.headers["Server-Timing"] = (f"retrieval;dur={(answer_start - start) * 1000:.1f}, "
                                         f"answer;dur={(end - answer_start) * 1000:.1f}")
    return PreviewReply(reply=result.reply)


app.mount("/api", budgetai_app)
app.mount("/preview", StaticFiles(directory=Path(__file__).resolve().parents[1] / "web", html=True))
