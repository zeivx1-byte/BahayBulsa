"""PyWebView methods for BudgetAI chat and plans, with local conversation history."""

from datetime import date
from pathlib import Path
import re
from tempfile import NamedTemporaryFile
from threading import Lock
from typing import Literal

from pydantic import BaseModel, Field, ValidationError

from budgeting_engine.calculations import money
from ledger_context import CHAT_EXAMPLE_PROFILE, build_ledger_request, prior_month_start
from main import BudgetOrganizerAPI
from model.ai_service import BudgetAIError
from model.budget_chat import ScenarioError
from model.budgeting_ai import BudgetAI
from model.schemas import ChatMemory, ChatRequest, ChatTurn, FinancialProfile, SavingsGoal
from storage import DATA_FILE


_HISTORY_LIMIT = 8
_MESSAGE_LIMIT = 1000
_PLAN_PROMPT = re.compile(
    r"\b(?:(?:create|generate|make|build|prepare|draft|give|suggest)\b[^.!?]*"
    r"\b(?:budget plan|monthly budget)|show\b[^.!?]*\bbudget plan)\b",
    re.IGNORECASE,
)
ChatSource = Literal["dashboard", "demo"]
_PLAN_REQUEST = "Create a monthly budget plan."


class _Conversation(BaseModel):
    history: list[ChatTurn] = Field(default_factory=list, max_length=_HISTORY_LIMIT)
    memory: ChatMemory | None = None


class _Conversations(BaseModel):
    dashboard: _Conversation = Field(default_factory=_Conversation)
    demo: _Conversation = Field(default_factory=_Conversation)


class DesktopBudgetOrganizerAPI(BudgetOrganizerAPI):
    def __init__(self, *, ai_factory=BudgetAI, history_path=None):
        super().__init__()
        self._ai_factory = ai_factory
        self._history_path = Path(history_path) if history_path is not None else Path(DATA_FILE).with_name("chat_history.json")
        self._chat_lock = Lock()

    def get_chat_history(self):
        with self._chat_lock:
            try:
                saved = self._load_conversations()
            except (OSError, ValidationError):
                return {"error": "Could not read the local chat history."}
            return {"histories": {source: conversation["history"] for source, conversation in saved.model_dump().items()}}

    def reset_chat(self, source: ChatSource):
        if source not in ("dashboard", "demo"):
            return {"error": "Choose a budget source."}
        with self._chat_lock:
            try:
                saved = self._load_conversations()
                setattr(saved, source, _Conversation())
                self._save_conversations(saved)
            except (OSError, ValidationError):
                return {"error": "Could not clear the local chat history."}
            return {"history": []}

    def ask_budget_ai(self, source: ChatSource, message: str):
        if not isinstance(message, str) or not 1 <= len(message.strip()) <= _MESSAGE_LIMIT:
            return {"error": "Enter a budget question of 1 to 1,000 characters."}
        message = message.strip()
        return self._answer(source, message, make_budget=bool(_PLAN_PROMPT.search(message)))

    def generate_budget_plan(self, source: ChatSource):
        return self._answer(source, _PLAN_REQUEST, make_budget=True)

    def _answer(self, source: ChatSource, message: str, *, make_budget: bool):
        if source not in ("dashboard", "demo"):
            return {"error": "Choose a budget source."}

        with self._chat_lock:
            try:
                conversations = self._load_conversations()
            except (OSError, ValidationError):
                return {"error": "Could not read the local chat history."}

            conversation = getattr(conversations, source)
            try:
                result, memory = self._respond(source, message, make_budget, conversation)
            except BudgetAIError as exc:
                return {"error": str(exc)}
            except ScenarioError as exc:
                return {"error": str(exc)}
            except ValueError:
                return {"error": "Check that the dashboard entries have valid dates, names, and amounts."}

            reply = result["reply"].strip()
            if not reply:
                return {"error": "BudgetAI returned an empty reply. Try again."}
            if "plan" in result and len(reply) > _MESSAGE_LIMIT:
                reply = f"{reply[:800]}…\n\n{reply[-190:]}"
            conversation.history = [
                *conversation.history,
                ChatTurn(role="user", content=message),
                ChatTurn(role="assistant", content=reply[:_MESSAGE_LIMIT]),
            ][-_HISTORY_LIMIT:]
            conversation.memory = memory
            try:
                self._save_conversations(conversations)
            except OSError:
                result["warning"] = "The answer was generated, but the local chat history could not be saved."
            return result

    def _respond(self, source: ChatSource, message: str, make_budget: bool, conversation: _Conversation):
        today = date.today()
        if source == "dashboard":
            request = self._dashboard_request(message, today, conversation, include_savings_target=make_budget)
        else:
            request = ChatRequest(profile=FinancialProfile.model_validate(CHAT_EXAMPLE_PROFILE), message=message,
                                  history=conversation.history)

        if make_budget and request.profile is None:
            return {"reply": "Add your income for this month to the dashboard to generate a budget plan."}, None

        with self._ai_factory() as ai:
            if make_budget:
                plan = ai.generate_budget(request.profile)
                return self._display_plan(plan, source, today), None
            if source == "dashboard":
                response = ai.respond_to_ledger_chat(request)
                return {"reply": response.reply}, response.memory
            return {"reply": ai.respond_to_chat(request).reply}, None

    def _dashboard_request(self, message: str, today: date, conversation: _Conversation, *, include_savings_target: bool):
        first_day = prior_month_start(today.replace(day=1)).isoformat()
        latest_day = today.isoformat()
        rows = [row for row in self.transactions.copy() if first_day <= row["date"] <= latest_day]
        goal = None
        if include_savings_target and self.savings_goal > 0:
            goal = SavingsGoal(name="Household savings", target_amount=self.savings_goal)
        return build_ledger_request(message, today, rows, self.bill_queue.get_sorted_bills(), goal,
                                    conversation.history, conversation.memory)

    @staticmethod
    def _display_plan(plan, source: ChatSource, today: date):
        parts = []
        if source == "dashboard":
            parts.append(f"This estimate uses dashboard entries posted this month through {today:%b %d, %Y}.")
            if plan.sections and plan.sections.financial_goals:
                parts.append("The dashboard stores a savings target. The estimate assumes a zero starting savings balance.")
        parts.append(plan.advice.summary)
        if plan.budget:
            savings = money(sum(line.amount for line in plan.budget if line.category in ("emergency_fund", "savings_goal")))
            parts.append(f"Planned savings and emergency fund contributions total {plan.currency} {savings:,.2f}.")
        return {"reply": "\n\n".join(parts), "plan": plan.model_dump(mode="json")}

    def _load_conversations(self) -> _Conversations:
        try:
            raw = self._history_path.read_bytes()
        except FileNotFoundError:
            return _Conversations()
        return _Conversations.model_validate_json(raw)

    def _save_conversations(self, saved: _Conversations):
        self._history_path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path = None
        try:
            with NamedTemporaryFile(mode="w", encoding="utf-8", dir=self._history_path.parent, delete=False) as file:
                temporary_path = Path(file.name)
                file.write(saved.model_dump_json(indent=2))
            temporary_path.replace(self._history_path)
        finally:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)
