"""BudgetAI: Python builds the budget, the AI explains it.

How generate_budget() works:
1-3. Python makes the complete plan (budgeting_engine/planner.py): exact
   facts, missing information, the budget from fixed rules (every amount is
   final and within income), notes and this month's action steps.
4. The Qwen model, through Ollama, receives the key facts, the final budget
   and Python's notes, and explains them in the AIBudgetAdvice JSON format:
   a summary, personal advice, suggested cuts, warnings and questions.
   An invalid reply is asked again.
5. Python checks the suggestions and questions and supplies warnings from
   the plan. The explanation uses Python's exact figures for shortfalls
   and incomplete budgets.

The AI interprets and recommends; Python does every calculation.
Nothing here changes the model's weights: this is prompt engineering.
"""
import re
import time
from copy import deepcopy
from pathlib import Path
from typing import Union

import ollama
from pydantic import ValidationError

import config
from budgeting_engine.calculations import plain
from budgeting_engine.insights import key_facts
from budgeting_engine.planner import make_plan
from budgeting_engine.validation import (
    adjustable_expenses, clean_ai_advice, data_notes, expense_review_tip, mentions_quantity, partial_plan_summary,
)
from model.budget_chat import answer_chat
from model.schemas import AIBudgetAdvice, BudgetPlan, BudgetResult, ChatFocus, ChatRequest, ChatResponse, FinancialProfile

TOLERANCE = 0.01          # one centavo
MAX_FORMAT_ATTEMPTS = 3   # tries to get a reply in the correct JSON format
MAX_ANSWER_TOKENS = 900   # upper limit on the length of one AI answer
RETRY_TEMPERATURE = 0.3   # a little variety on retries, so the answer changes
ANSWER_SCHEMA = AIBudgetAdvice.model_json_schema()
CHAT_FOCUS_SCHEMA = ChatFocus.model_json_schema()
THOUSANDS_COMMA = re.compile(r"(\d),(?=\d{3}(?:\D|$))")   # the comma in 4,000 or 10,401.00
FUND_PRAISE = re.compile(
    r"\b(?:healthy|adequate|strong|solid|sufficient|comfortable)\s+(?:emergency fund|(?:cash )?buffer|cushion|reserve)\b"
    r"|\bemergency fund\s+(?:is|looks|seems)\s+(?:already\s+)?(?:healthy|adequate|strong|solid|sufficient|comfortable)\b",
    re.IGNORECASE,
)
DEFICIT_CLAIM = re.compile(r"\b(?:deficit|shortfall|short of required spending)\b", re.IGNORECASE)
UNFUNDED_ADVICE = re.compile(
    r"\b(?:(?:spare|extra|leftover|remaining|unspent|unused|unallocated)\s+(?:cash|money|funds|income)"
    r"|(?:cash|money|funds|income)\s+(?:left(?:\s+over)?|to spare)"
    r"|(?:use|move|transfer|redirect|allocate)\s+(?:the\s+|your\s+)?(?:surplus|remaining balance)"
    r"|(?:add|make)\s+(?:an?\s+)?(?:extra|additional)\s+(?:amount|contribution|deposit)"
    r"|(?:increase|raise|boost)(?:\s+[\w'-]+){0,4}\s+(?:savings|payments?|contributions?)"
    r"|(?:save|contribute)\s+more|pay\s+more\s+(?:on|toward))\b",
    re.IGNORECASE,
)
NEW_FUNDING = re.compile(
    r"\b(?:if|once|when|after)\s+(?:(?:you|your household)\s+)?(?:can\s+)?"
    r"(?:cut|reduce|lower|trim|cancel|reallocate|earn (?:extra|additional|more)|receive (?:a bonus|a refund))\b"
    r"|\b(?:by|from)\s+(?:cutting|reducing|lowering|trimming|cancell?ing|reallocating|earning (?:extra|additional|more))\b",
    re.IGNORECASE,
)
NO_EXTRA_FUNDS = re.compile(r"\b(?:no|not|without)\s+(?:any\s+)?$", re.IGNORECASE)
PRESENT_FUNDS_CLAIM = re.compile(r"\b(?:you have|there is|there are)\s+(?:\w+\s+){0,4}$", re.IGNORECASE)


def _assumes_unallocated_money(savings: str) -> bool:
    """Find advice that treats fully assigned income as available for new allocations."""
    for sentence in re.split(r"(?<=[.!?;])\s+", savings):
        source = NEW_FUNDING.search(sentence)
        for claim in UNFUNDED_ADVICE.finditer(sentence):
            before_claim = sentence[:claim.start()]
            if NO_EXTRA_FUNDS.search(before_claim):
                continue
            if source and (source.start() < claim.start() or not PRESENT_FUNDS_CLAIM.search(before_claim)):
                continue
            return True
    return False


def _shortfall_warning(plan: BudgetPlan) -> str:
    spending = "known minimum debt payments" if plan.is_partial else "required spending"
    return f"Monthly planning income is {plan.currency} {plan.facts.shortfall:,.2f} short of {spending}."


def _verified_warnings(profile: FinancialProfile, plan: BudgetPlan) -> list[str]:
    warnings = [_shortfall_warning(plan)] if plan.facts.is_deficit else []
    months = plan.facts.emergency_fund_months
    if plan.facts.situation != "incomplete" and months is not None and months < 1:
        warnings.append("Your emergency fund covers less than one month of required spending.")
    return warnings + data_notes(profile)


def _reply_format(profile: FinancialProfile, plan: BudgetPlan) -> dict:
    """Constrain structured fields to expenses and warnings from this plan."""
    schema = deepcopy(ANSWER_SCHEMA)
    fields = schema["properties"]
    warnings = _verified_warnings(profile, plan)
    fields["warnings"]["maxItems"] = len(warnings)
    if warnings:
        fields["warnings"]["items"]["enum"] = [THOUSANDS_COMMA.sub(r"\1", warning) for warning in warnings]
    fields["missing_information"]["maxItems"] = len(plan.missing_data)

    adjustable = adjustable_expenses(profile)
    fields["spending_flags"]["maxItems"] = int(bool(adjustable))
    if adjustable:
        flag = schema["$defs"]["SpendingFlag"]["properties"]
        flag["expense_name"]["enum"] = [expense.name for expense in adjustable]
        flag["reason"]["enum"] = list(dict.fromkeys(expense_review_tip(expense) for expense in adjustable))
    return schema


class BudgetAIError(Exception):
    """A problem in BudgetAI. The message says what to do."""


class ModelUnavailableError(BudgetAIError):
    """Ollama is not running, or the model is not downloaded."""


class InvalidModelResponseError(BudgetAIError):
    """The model kept answering in an invalid format."""


def load_instructions(path: Path, currency: str) -> str:
    """Read model/instructions.txt and fill in the currency."""
    try:
        text = Path(path).read_text(encoding="utf-8")
    except FileNotFoundError:
        raise BudgetAIError(f"Instructions file not found: {path}") from None
    if not text.strip():
        raise BudgetAIError(f"Instructions file is empty: {path}")
    return text.replace("{currency}", currency)


class BudgetAI:
    """Builds budgets with Python rules and explains them with the AI model."""

    def __init__(self, settings=None, client=None):
        self.settings = settings or config.settings
        self.client = client or ollama.Client(
            host=self.settings.ollama_host, timeout=self.settings.timeout_seconds
        )
        self.instructions = load_instructions(self.settings.instructions_path, self.settings.currency)

    # ------------------------------------------------------------ public
    def generate_budget(self, profile: Union[FinancialProfile, dict]) -> BudgetResult:
        """Return a monthly budget for one household, explained by the AI.

        Raises pydantic.ValidationError if the profile itself is invalid,
        or a BudgetAIError if the AI cannot produce a usable answer.
        """
        if isinstance(profile, dict):
            profile = FinancialProfile.model_validate(profile)
        plan = make_plan(profile, self.settings.currency)

        messages = [
            {"role": "system", "content": self.instructions},
            {"role": "user", "content": self._build_request(profile, plan)},
        ]
        start = time.perf_counter()
        advice, attempts = self._ask_for_advice(messages, _reply_format(profile, plan))
        advice, cleanup = clean_ai_advice(advice, profile, plan.budget, plan.missing_data, self.settings.currency)
        advice = self._ground_explanation(advice, profile, plan)
        return self._build_result(plan, advice, cleanup, attempts, time.perf_counter() - start)

    def respond_to_chat(self, request: ChatRequest) -> ChatResponse:
        """Reply with verified planner facts or a separate what-if plan."""
        return answer_chat(request, self.settings.currency, self._choose_chat_focus)

    # ------------------------------------------------------------ talking to the model
    def _choose_chat_focus(self, messages: list[dict[str, str]]) -> ChatFocus:
        for attempt in range(MAX_FORMAT_ATTEMPTS):
            temperature = self.settings.temperature if attempt == 0 else max(self.settings.temperature, RETRY_TEMPERATURE)
            try:
                return ChatFocus.model_validate_json(self._chat(messages, temperature, CHAT_FOCUS_SCHEMA))
            except (ValidationError, InvalidModelResponseError):
                continue
        raise InvalidModelResponseError("The model could not select a budget detail to explain.")

    def _ask_for_advice(self, messages: list, reply_format: dict) -> tuple:
        """Ask until the reply is valid. Returns (advice, attempts)."""
        problem = ""
        repetitions = 0
        for attempt in range(1, MAX_FORMAT_ATTEMPTS + 1):
            temperature = self.settings.temperature if attempt == 1 else max(self.settings.temperature, RETRY_TEMPERATURE)
            try:
                raw = self._chat(messages, temperature, reply_format if repetitions < 2 else ANSWER_SCHEMA)
            except InvalidModelResponseError as exc:
                problem = str(exc)
                repetitions += 1
                continue
            try:
                return AIBudgetAdvice.model_validate_json(raw), attempt
            except ValidationError as exc:
                first = exc.errors()[0]
                where = ".".join(str(part) for part in first["loc"]) or "reply"
                problem = f"{where}: {first['msg']}"
        raise InvalidModelResponseError(
            f"The model gave an invalid answer {MAX_FORMAT_ATTEMPTS} times in a row. Last problem: {problem}"
        )

    def _chat(self, messages: list, temperature: float, reply_format: dict) -> str:
        """Send one request to Ollama and return the reply text."""
        model = self.settings.model_name
        try:
            response = self.client.chat(
                model=model,
                messages=messages,
                format=reply_format,
                options={
                    "temperature": temperature,
                    "num_ctx": self.settings.context_tokens,
                    "num_predict": MAX_ANSWER_TOKENS,
                },
                think=False,
            )
        except ConnectionError:
            raise ModelUnavailableError(
                f"Cannot reach Ollama at {self.settings.ollama_host}. Open Ollama from the Start menu and try again."
            ) from None
        except ollama.ResponseError as exc:
            if exc.status_code == 404:
                raise ModelUnavailableError(f"Model {model} is not downloaded. Run:  ollama pull {model}") from None
            if "token repeat limit reached" in exc.error.lower():
                raise InvalidModelResponseError("The model stalled while writing its reply.") from None
            raise BudgetAIError(f"Ollama returned an error: {exc.error}") from None
        except Exception as exc:
            raise BudgetAIError(
                f"The AI request failed ({type(exc).__name__}: {exc}). "
                "If it timed out, increase BUDGETAI_TIMEOUT_SECONDS in .env."
            ) from None
        return response.message.content or ""

    # ------------------------------------------------------------ building the request
    def _build_request(self, profile: FinancialProfile, plan: BudgetPlan) -> str:
        # Numbers are written without commas (4000, not 4,000): see calculations.plain().
        cur = self.settings.currency
        facts, lines, notes, missing = plan.facts, plan.budget, plan.rule_notes, plan.missing_data
        if lines:
            budget = "\n".join(
                f"- {line.name} | funded {plain(line.amount)}"
                + (f" | needed {plain(line.needed_amount)}" if line.amount < line.needed_amount else "")
                for line in sorted(lines, key=lambda l: l.priority)
            )
            budget += f"\nTotal funded: {plain(plan.total_allocated)} of {plain(facts.planning_income)} income"
        else:
            budget = "none (no expenses are listed yet)"
        fact_sentences = key_facts(profile, facts)
        facts_text = "\n".join(f"- {sentence}" for sentence in fact_sentences)
        warnings_text = "\n".join(f"- {warning}" for warning in _verified_warnings(profile, plan)) or "none"
        expense_text = "\n".join(f"- {expense.name} | current {plain(expense.monthly_amount)} | "
                                 f"reason: {expense_review_tip(expense)}"
                                 for expense in adjustable_expenses(profile)) or "none"
        notes_text = "\n".join(f"- {n}" for n in notes) if notes else "none"
        missing_text = "\n".join(f"- {m}" for m in missing) if missing else "none"
        if plan.is_partial:
            summary_focus = 'Start with "This is a partial plan". Explain which minimum debt payments it covers.'
        else:
            summary_focus = {
                "incomplete": "Ask for monthly expenses so a budget can be built.",
                "deficit": "Some required spending is unfunded. Explain that essentials are prioritized.",
                "tight": "Required spending is covered and little remains for other priorities.",
                "stable": "Required spending is covered. Refer to priorities shown in the final budget.",
            }[facts.situation]
        household = ", ".join(part for part in (
            f"{profile.household_size} people" if profile.household_size else "",
            profile.location or "",
            f"note from the user: {profile.notes}" if profile.notes else "",
        ) if part) or "no details given"
        prompt = (
            f"Explain this household's monthly budget. All amounts are in {cur}.\n\n"
            f"SITUATION: {facts.situation}"
            + (" (Partial plan: only the known minimum debt payments are budgeted)" if plan.is_partial else "")
            + f"\nSUMMARY FOCUS: {summary_focus}\n\n"
            f"KEY FACTS (calculated by Python; exact):\n{facts_text}\n\n"
            f"FINAL BUDGET (built by Python):\n{budget}\n"
            f"Unallocated after this plan: {plain(plan.unallocated)}\n\n"
            f"ADJUSTABLE EXPENSES:\n{expense_text}\n\n"
            f"VERIFIED WARNINGS:\n{warnings_text}\n\n"
            f"PYTHON NOTES:\n{notes_text}\n\n"
            f"MISSING DATA:\n{missing_text}\n\n"
            f"HOUSEHOLD: {household}"
        )
        # Reasons and notes are written for people ("4,000.00"); remove those commas for the AI too.
        return THOUSANDS_COMMA.sub(r"\1", prompt)

    # ------------------------------------------------------------ the result
    def _ground_explanation(self, advice: AIBudgetAdvice, profile: FinancialProfile, plan: BudgetPlan) -> AIBudgetAdvice:
        """Use the plan for critical warnings and replace unsafe savings amounts."""
        facts = plan.facts
        months = facts.emergency_fund_months
        weak_fund = facts.situation != "incomplete" and months is not None and months < 1
        warnings = _verified_warnings(profile, plan)

        savings = advice.savings_advice
        if facts.situation == "incomplete":
            summary = (partial_plan_summary(profile, plan.budget, plan.currency) if plan.is_partial
                       else "List your monthly expenses so BudgetAI can build your budget.")
            savings = "Add your expenses so you can see how much will be available for savings."
        elif facts.is_deficit:
            summary = ("Monthly income does not cover required spending. This budget prioritizes essentials "
                       "and shows how much of each bill can be paid.")
            savings = ("Follow the budget for your essentials first. Look for lower variable costs or extra income "
                       "to close the gap.")
        else:
            summary = (f"Monthly planning income of {plan.currency} {facts.planning_income:,.2f} covers "
                       f"{plan.currency} {facts.required_spending:,.2f} in required spending. ")
            summary += ("Little remains after those costs." if facts.situation == "tight" else
                        "See the budget for amounts assigned to other priorities.")
            if plan.unallocated <= 0 and _assumes_unallocated_money(savings):
                savings = ("All planning income is assigned in this budget. Follow its allocations. "
                           "Before increasing savings or debt payments, reduce a planned cost or add income.")
            elif (not savings.strip() or mentions_quantity(savings) or DEFICIT_CLAIM.search(savings)
                    or (weak_fund and FUND_PRAISE.search(savings))):
                fund = next((line for line in plan.budget if line.category == "emergency_fund" and line.amount > 0), None)
                if fund is not None:
                    savings = f"This month, move {plan.currency} {fund.amount:,.2f} to your emergency fund as shown in the budget."
                else:
                    savings = "Follow the budget's savings amounts. To save more, identify a cost to reduce or income to add first."

        return advice.model_copy(update={"summary": summary, "savings_advice": savings, "warnings": warnings})

    def _build_result(self, plan: BudgetPlan, advice, cleanup, attempts, seconds) -> BudgetResult:
        cur = self.settings.currency
        facts = plan.facts
        within = plan.total_allocated <= facts.planning_income + TOLERANCE

        checks = []
        if within:
            checks.append(f"OK: the budget ({plan.total_allocated:,.2f} {cur}) is within planning income "
                          f"({facts.planning_income:,.2f} {cur}).")
        else:
            checks.append(f"PROBLEM: the budget is {-plan.unallocated:,.2f} {cur} more than planning income.")
        if facts.is_deficit:
            checks.append(f"DEFICIT: required spending is {facts.shortfall:,.2f} {cur} more than planning income; "
                          f"{plan.unfunded:,.2f} {cur} of required spending is unfunded.")
        if plan.unallocated > TOLERANCE:
            checks.append(f"NOTE: {plan.unallocated:,.2f} {cur} is not allocated.")
        if plan.is_partial:
            checks.append(f"NOTE: partial plan. No expenses are listed, so only the known minimum debt payments "
                          f"({plan.total_allocated:,.2f} {cur}) are budgeted.")
        if plan.missing_data:
            checks.append(f"NOTE: {len(plan.missing_data)} piece(s) of information are missing; see the questions.")

        return BudgetResult(
            currency=cur, facts=facts, budget=plan.budget, total_allocated=plan.total_allocated,
            unallocated=plan.unallocated, unfunded=plan.unfunded, within_income=within, is_partial=plan.is_partial,
            rule_notes=plan.rule_notes + cleanup, action_steps=plan.action_steps, checks=checks,
            missing_data=plan.missing_data, advice=advice, model=self.settings.model_name,
            attempts=attempts, seconds=round(seconds, 1),
        )
