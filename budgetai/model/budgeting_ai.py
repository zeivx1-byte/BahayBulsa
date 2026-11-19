"""BudgetAI: Python builds the budget, the AI explains it.

How generate_budget() works:
1-3. Python makes the complete plan (budgeting_engine/planner.py): exact
   facts, missing information, the budget from fixed rules (every amount is
   final and within income), notes and this month's action steps.
4. The Groq-hosted model receives the key facts, the final budget
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
from collections import Counter
from collections.abc import Iterable
from copy import deepcopy
from pathlib import Path
from typing import Union

import httpx
from pydantic import ValidationError

import config
from budgeting_engine.calculations import money, plain
from budgeting_engine.insights import key_facts
from budgeting_engine.planner import make_plan
from budgeting_engine.validation import (
    adjustable_expenses, clean_ai_advice, data_notes, expense_review_tip, mentions_quantity, partial_plan_summary,
)
from model.ai_service import (
    BudgetAIError as BudgetAIError, GroqAIService, InvalidModelResponseError as InvalidModelResponseError,
    ModelConfigurationError as ModelConfigurationError, ModelRateLimitError as ModelRateLimitError,
    ModelTimeoutError as ModelTimeoutError, ModelUnavailableError as ModelUnavailableError, redact_identifiers,
)
from model.budget_chat import answer_chat, answer_ledger_chat
from model.schemas import (
    AIBudgetAdvice, BudgetAllocationRecommendation, BudgetExpenseOverview, BudgetGoalOverview, BudgetIncomeOverview,
    BudgetPlan, BudgetPlanSections, BudgetResult, ChatFocus, ChatRequest, ChatResponse, FinancialProfile,
    LedgerChatRequest, LedgerChatResponse,
)

TOLERANCE = 0.01          # one centavo
MAX_FORMAT_ATTEMPTS = 3   # tries to get a reply in the correct JSON format
MAX_ANSWER_TOKENS = 1400  # includes provider reasoning tokens
MAX_CHAT_FOCUS_TOKENS = 650
MAX_ADJUSTABLE_EXPENSES = 6
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
    fields["warnings"]["maxItems"] = min(3, len(warnings))
    if warnings:
        fields["warnings"]["items"]["enum"] = list(dict.fromkeys(
            redact_identifiers(THOUSANDS_COMMA.sub(r"\1", warning)) for warning in warnings[:3]
        ))
    fields["missing_information"]["maxItems"] = min(3, len(plan.missing_data))

    adjustable = adjustable_expenses(profile)[:MAX_ADJUSTABLE_EXPENSES]
    fields["spending_flags"]["maxItems"] = int(bool(adjustable))
    if adjustable:
        flag = schema["$defs"]["SpendingFlag"]["properties"]
        flag["expense_name"]["enum"] = list(dict.fromkeys(redact_identifiers(expense.name) for expense in adjustable))
        flag["reason"]["enum"] = list(dict.fromkeys(expense_review_tip(expense) for expense in adjustable))
    return schema


def _prompt_list(items: Iterable[str], byte_limit: int) -> str:
    """Keep whole facts in the model prompt. The API response still has the full plan."""
    lines = []
    for item in items:
        line = f"- {item}"
        byte_limit -= len(line.encode("utf-8")) + 1
        if byte_limit < 0:
            lines.append("Additional details are in the full Python plan.")
            break
        lines.append(line)
    return "\n".join(lines) or "none"


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
        self.client = client if client is not None else GroqAIService(self.settings)
        self.instructions = load_instructions(self.settings.instructions_path, self.settings.currency)

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    def close(self) -> None:
        close = getattr(self.client, "close", None)
        if close is not None:
            close()

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
        return self._build_result(profile, plan, advice, cleanup, attempts, time.perf_counter() - start)

    def respond_to_chat(self, request: ChatRequest) -> ChatResponse:
        """Reply with verified planner or spending facts, or a separate what-if plan."""
        return answer_chat(request, self.settings.currency, self._choose_chat_focus)

    def respond_to_ledger_chat(self, request: LedgerChatRequest) -> LedgerChatResponse:
        """Answer with the ledger and recent conversation supplied for this question."""
        return answer_ledger_chat(request, self.settings.currency, self._choose_chat_focus)

    # ------------------------------------------------------------ talking to the model
    def _choose_chat_focus(self, messages: list[dict[str, str]]) -> ChatFocus:
        for attempt in range(MAX_FORMAT_ATTEMPTS):
            temperature = self.settings.temperature if attempt == 0 else max(self.settings.temperature, RETRY_TEMPERATURE)
            try:
                raw = self._chat(messages, temperature, CHAT_FOCUS_SCHEMA, MAX_CHAT_FOCUS_TOKENS)
                return ChatFocus.model_validate_json(raw)
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

    def _chat(self, messages: list, temperature: float, reply_format: dict, max_tokens: int = MAX_ANSWER_TOKENS) -> str:
        """Send one request and return only the provider's final reply text."""
        try:
            response = self.client.chat(
                model=self.settings.model_name,
                messages=messages,
                schema=reply_format,
                temperature=temperature,
                max_completion_tokens=max_tokens,
            )
        except (TimeoutError, httpx.TimeoutException):
            raise ModelTimeoutError(
                "Groq did not answer in time. Check BUDGETAI_TIMEOUT_SECONDS or try again shortly."
            ) from None
        except (ConnectionError, httpx.TransportError):
            raise ModelUnavailableError(
                "Could not reach Groq. Check your internet connection and try again."
            ) from None
        content = getattr(response, "content", None)
        if not isinstance(content, str) or not content.strip():
            raise InvalidModelResponseError("Groq returned an empty or incomplete reply.")
        return content

    # ------------------------------------------------------------ building the request
    def _build_request(self, profile: FinancialProfile, plan: BudgetPlan) -> str:
        # Numbers are written without commas (4000, not 4,000): see calculations.plain().
        cur = self.settings.currency
        facts, lines, notes, missing = plan.facts, plan.budget, plan.rule_notes, plan.missing_data
        if lines:
            budget_items = (
                f"{line.name} | funded {plain(line.amount)}"
                + (f" | needed {plain(line.needed_amount)}" if line.amount < line.needed_amount else "")
                for line in sorted(lines, key=lambda l: (l.priority, -l.needed_amount))
            )
            budget = _prompt_list(budget_items, 1400)
            budget += f"\nTotal funded: {plain(plan.total_allocated)} of {plain(facts.planning_income)} income"
        else:
            budget = "none (no expenses are listed yet)"
        facts_text = _prompt_list(key_facts(profile, facts), 1200)
        warnings_text = _prompt_list(_verified_warnings(profile, plan)[:3], 650)
        expense_items = (f"{expense.name} | current {plain(expense.monthly_amount)} | reason: {expense_review_tip(expense)}"
                         for expense in adjustable_expenses(profile)[:MAX_ADJUSTABLE_EXPENSES])
        expense_text = _prompt_list(expense_items, 650)
        notes_text = _prompt_list(notes, 450)
        missing_text = _prompt_list(missing, 450)
        if plan.is_partial:
            summary_focus = 'Start with "This is a partial plan". Explain which minimum debt payments it covers.'
        else:
            summary_focus = {
                "incomplete": "Ask for monthly expenses so a budget can be built.",
                "deficit": "Some required spending is unfunded. Explain that essentials are prioritized.",
                "tight": "Required spending is covered and little remains for other priorities.",
                "stable": "Required spending is covered. Refer to priorities shown in the final budget.",
            }[facts.situation]
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
            f"MISSING DATA:\n{missing_text}"
        )
        # Reasons and notes are written for people ("4,000.00"); remove those commas for the AI too.
        return redact_identifiers(THOUSANDS_COMMA.sub(r"\1", prompt))

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
            summary = ("Monthly income does not cover required spending. "
                       f"The shortfall is {plan.currency} {facts.shortfall:,.2f}, so the plan puts essentials first.")
            savings = ("Follow the budget for your essentials first. Look for lower variable costs or extra income "
                       "to close the gap.")
        else:
            summary = (f"Monthly planning income of {plan.currency} {facts.planning_income:,.2f} covers "
                       f"{plan.currency} {facts.required_spending:,.2f} in required spending. ")
            priority = next((line for line in plan.budget if line.source == "rule" and line.amount > 0
                             and line.category in {"emergency_fund", "debt_payment", "savings_goal"}), None)
            if priority is not None:
                summary += f"The plan sets aside {plan.currency} {priority.amount:,.2f} for {priority.name}."
            elif facts.situation == "tight":
                summary += "Little remains after those costs."
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

        explanation = advice.explanation
        if explanation and (mentions_quantity(explanation) or _assumes_unallocated_money(explanation)):
            explanation = None
        if explanation is not None and (not explanation.strip() or facts.situation == "incomplete"):
            explanation = None
        return advice.model_copy(update={"summary": summary, "savings_advice": savings, "warnings": warnings,
                                         "explanation": explanation})

    def _build_result(self, profile: FinancialProfile, plan: BudgetPlan, advice: AIBudgetAdvice,
                      cleanup: list[str], attempts: int, seconds: float) -> BudgetResult:
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
            missing_data=plan.missing_data, advice=advice, sections=_plan_sections(profile, plan, advice),
            model=self.settings.model_name,
            attempts=attempts, seconds=round(seconds, 1),
        )


def _plan_sections(profile: FinancialProfile, plan: BudgetPlan, advice: AIBudgetAdvice) -> BudgetPlanSections:
    facts = plan.facts
    goal_amounts = {line.name: line.amount for line in plan.budget if line.category == "savings_goal"}
    goal_counts = Counter(goal.name for goal in profile.savings_goals)
    return BudgetPlanSections(
        summary=advice.summary,
        income_overview=BudgetIncomeOverview(monthly_income=facts.total_income, planning_income=facts.planning_income),
        expense_overview=BudgetExpenseOverview(
            monthly_estimate=facts.total_expenses, fixed=facts.fixed_expenses, variable=facts.variable_expenses,
            required=facts.required_spending, minimum_debt_payments=facts.minimum_debt_payments,
        ),
        recommended_allocations=[BudgetAllocationRecommendation(
            name=line.name, category=line.category, amount=line.amount,
            percentage_of_planning_income=money(line.amount / facts.planning_income * 100) if facts.planning_income else 0,
        ) for line in plan.budget],
        savings_recommendations=advice.savings_advice,
        spending_adjustments=advice.spending_flags,
        financial_goals=[BudgetGoalOverview(
            name=goal.name, target=goal.target_amount, saved=goal.current_amount,
            planned_monthly_contribution=(goal_amounts.get(f"Savings goal: {goal.name}", 0)
                                          if goal_counts[goal.name] == 1 else None),
        ) for goal in profile.savings_goals],
        explanation=advice.explanation or advice.savings_advice,
    )
