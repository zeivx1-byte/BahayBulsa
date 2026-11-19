"""Data formats (schemas) for BudgetAI.

INPUT  - FinancialProfile: a household's income, expenses, debts and goals.
AI     - AIBudgetAdvice: the exact JSON shape of the AI's explanation.
RESULT - BudgetResult: Python's facts and budget plus the AI's advice.

pydantic checks every value automatically: wrong types, negative money,
unknown categories or missing fields are rejected with a clear error.
All amounts are per month, in the currency set in .env (PHP by default).

MONEY POLICY: every money amount in a profile is rounded to the nearest
centavo as soon as it is read, with half a centavo rounding up
(0.006 -> 0.01, 0.004 -> 0.00, 2.675 -> 2.68). After that every calculation
works in whole centavos, so displayed budget lines always add up to the
displayed total. An income or goal target that rounds to 0.00 is rejected.
"""
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import Annotated, Literal, Optional

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, model_validator

# ---------------------------------------------------------------- categories
ExpenseCategory = Literal[
    "housing",                  # rent, amortization, association dues
    "food",                     # groceries, market, rice, water refills
    "utilities",                # electricity, water, internet, LPG, phone load
    "transportation",           # fare, fuel, parking, tolls
    "health",                   # medicine, check-ups, vitamins
    "education",                # tuition, school supplies, baon
    "insurance_contributions",  # SSS, PhilHealth, Pag-IBIG, insurance premiums
    "family_support",           # padala to parents, family allowances
    "personal_care",            # haircut, salon, cosmetics
    "shopping",                 # clothes, gadgets, online shopping
    "dining_out",               # restaurants, fast food, food delivery
    "entertainment",            # movies, outings, games
    "subscriptions",            # Netflix, Spotify, apps
    "other",
]

# Where a budget line can go. Debt payments and savings are not expenses,
# so they have their own categories here.
AllocationCategory = Literal[
    "housing", "food", "utilities", "transportation", "health", "education",
    "insurance_contributions", "family_support", "personal_care", "shopping",
    "dining_out", "entertainment", "subscriptions", "other",
    "debt_payment", "emergency_fund", "savings_goal",
]

ExpenseKind = Literal["essential", "obligation", "non_essential"]

MAX_MONEY = 100_000_000  # sanity limit: no single amount above 100 million


def to_centavos(value: float) -> float:
    """Round to the nearest centavo, half up: 0.006 -> 0.01, 0.004 -> 0.00, 2.675 -> 2.68.

    Decimal(str(value)) uses the number as written, so 2.675 is not mistaken
    for 2.67499999... the way plain float rounding would.
    """
    return float(Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def _at_least_one_centavo(value: float) -> float:
    if value < 0.01:
        raise ValueError("must be at least 0.01 after rounding to the nearest centavo")
    return value


# Money: 0 or more, rounded to centavos. PositiveMoney: at least 0.01 after rounding.
Money = Annotated[float, Field(ge=0, le=MAX_MONEY), AfterValidator(to_centavos)]
PositiveMoney = Annotated[float, Field(gt=0, le=MAX_MONEY), AfterValidator(to_centavos),
                          AfterValidator(_at_least_one_centavo)]


class _Strict(BaseModel):
    """Base for input models: unknown fields are an error, not ignored."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


# ---------------------------------------------------------------- input
class IncomeSource(_Strict):
    name: str = Field(min_length=1, max_length=80)
    monthly_amount: PositiveMoney
    regular: bool = True
    # For irregular income: the lowest monthly amount in recent months.
    minimum_monthly_amount: Optional[Money] = None

    @model_validator(mode="after")
    def _minimum_not_above_average(self):
        if self.minimum_monthly_amount is not None and self.minimum_monthly_amount > self.monthly_amount:
            raise ValueError("minimum_monthly_amount cannot be more than monthly_amount")
        return self


class Expense(_Strict):
    name: str = Field(min_length=1, max_length=80)
    category: ExpenseCategory
    monthly_amount: Money
    kind: Optional[ExpenseKind] = None   # None = decided from the category
    fixed: Optional[bool] = None         # None = decided from the category


class Debt(_Strict):
    name: str = Field(min_length=1, max_length=80)
    balance: Money
    minimum_payment: Money   # a minimum above the balance is flagged, not rejected
    annual_interest_rate_percent: Optional[float] = Field(default=None, ge=0, le=1000)


class SavingsGoal(_Strict):
    name: str = Field(min_length=1, max_length=80)
    target_amount: PositiveMoney
    current_amount: Money = 0
    months_left: Optional[int] = Field(default=None, ge=1, le=600)


class FinancialProfile(_Strict):
    incomes: list[IncomeSource] = Field(min_length=1, max_length=20)
    expenses: list[Expense] = Field(default_factory=list, max_length=100)
    debts: list[Debt] = Field(default_factory=list, max_length=30)
    savings_goals: list[SavingsGoal] = Field(default_factory=list, max_length=20)
    emergency_fund_balance: Money = 0
    household_size: Optional[int] = Field(default=None, ge=1, le=30)
    location: Optional[str] = Field(default=None, max_length=80)
    notes: Optional[str] = Field(default=None, max_length=500)


# ---------------------------------------------------------------- AI output
class SpendingFlag(BaseModel):
    expense_name: str
    current_amount: float = Field(ge=0)
    suggested_amount: float = Field(ge=0)
    reason: str


class AIBudgetAdvice(BaseModel):
    """The JSON shape the AI is forced to answer in. It explains; it sets no amounts."""

    summary: str
    savings_advice: str
    spending_flags: list[SpendingFlag]
    warnings: list[str]
    missing_information: list[str]


# ---------------------------------------------------------------- facts, budget, result
class FinancialSummary(BaseModel):
    """Exact numbers calculated by Python (never by the AI)."""

    total_income: float
    planning_income: float
    has_irregular_income: bool
    total_expenses: float
    essential_expenses: float
    obligation_expenses: float
    non_essential_expenses: float
    fixed_expenses: float
    variable_expenses: float
    minimum_debt_payments: float
    total_debt: float
    required_spending: float
    balance_after_required: float
    balance_after_all_spending: float
    is_deficit: bool
    shortfall: float
    emergency_fund_months: Optional[float]
    situation: Literal["incomplete", "deficit", "tight", "stable"]


class BudgetLine(BaseModel):
    """One line of the final budget."""

    name: str
    category: AllocationCategory
    priority: int = Field(ge=1, le=6)
    amount: float = Field(ge=0)
    needed_amount: float = Field(ge=0)
    source: Literal["required", "rule"]   # required spending, or set by a budgeting rule
    reason: str


class BudgetPlan(BaseModel):
    """The rule-based budget for one household, made by Python alone (no AI)."""

    @property
    def is_partial(self) -> bool:
        """No expenses are listed, but some payments (minimum debt payments) are known and budgeted."""
        return self.facts.situation == "incomplete" and bool(self.budget)

    rules_version: str
    currency: str
    facts: FinancialSummary
    budget: list[BudgetLine]
    total_allocated: float
    unallocated: float
    unfunded: float
    rule_notes: list[str]
    action_steps: list[str]
    missing_data: list[str]


class BudgetResult(BaseModel):
    """What BudgetAI returns: Python's budget, explained by the AI."""

    currency: str
    facts: FinancialSummary
    budget: list[BudgetLine]
    total_allocated: float
    unallocated: float
    unfunded: float
    within_income: bool
    is_partial: bool
    rule_notes: list[str]
    action_steps: list[str]
    checks: list[str]
    missing_data: list[str]
    advice: AIBudgetAdvice
    model: str
    attempts: int
    seconds: float


class ChatTurn(_Strict):
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=1000)


class ExpenseChange(_Strict):
    expense_name: str = Field(min_length=1, max_length=80)
    operation: Literal["set", "increase", "decrease"]
    amount: Money

    @model_validator(mode="after")
    def _nonzero_delta(self):
        if self.operation != "set" and self.amount == 0:
            raise ValueError("An increase or decrease must be at least one centavo.")
        return self


class ExpenseTransaction(_Strict):
    """A posted expense in the configured currency."""

    posted_on: date
    name: str = Field(min_length=1, max_length=80)
    category: ExpenseCategory
    amount: PositiveMoney


class UpcomingBill(_Strict):
    name: str = Field(min_length=1, max_length=80)
    due_on: date
    amount: PositiveMoney
    recurring: bool = False


class SpendingRecords(_Strict):
    """Complete posted expense coverage for one household over an inclusive date range."""

    as_of: date
    coverage_start: date
    transactions: list[ExpenseTransaction] = Field(max_length=5000)
    category_limits: dict[ExpenseCategory, Money] = Field(default_factory=dict)
    monthly_budget: Money | None = None
    previous_category_limits: dict[ExpenseCategory, Money] = Field(default_factory=dict)
    previous_monthly_budget: Money | None = None
    income_total: Money | None = None
    previous_income_total: Money | None = None
    upcoming_bills: list[UpcomingBill] | None = Field(default=None, max_length=500)
    savings_goal: SavingsGoal | None = None

    @model_validator(mode="after")
    def _validate_dates(self):
        if self.coverage_start > self.as_of:
            raise ValueError("coverage_start must be on or before as_of")
        if any(not self.coverage_start <= item.posted_on <= self.as_of for item in self.transactions):
            raise ValueError("transaction dates must be inside the declared coverage range")
        return self


class ChatRequest(_Strict):
    profile: FinancialProfile
    message: str = Field(min_length=1, max_length=1000)
    history: list[ChatTurn] = Field(default_factory=list, max_length=8)
    change: ExpenseChange | None = None
    records: SpendingRecords | None = None


class ChatScenario(BaseModel):
    expense_name: str
    previous_monthly_amount: float
    new_monthly_amount: float
    plan: BudgetPlan


class ChatResponse(BaseModel):
    reply: str
    plan: BudgetPlan
    scenario: ChatScenario | None = None


class ChatMemory(_Strict):
    """Server-owned reference to the last verified topic, with no financial values."""

    topic: Literal[
        "spending", "largest_category", "transaction", "expense_trend", "spending_comparison", "income",
        "monthly_budget", "category_limit",
    ]
    category: ExpenseCategory | None = None
    expense_name: str | None = Field(default=None, min_length=1, max_length=80)
    period: Literal["current", "previous"] = "current"

    @model_validator(mode="after")
    def _single_target(self):
        if self.category is not None and self.expense_name is not None:
            raise ValueError("memory may reference one category or expense")
        return self


class LedgerChatRequest(ChatRequest):
    """Internal chat input built from an account's ledger and conversation."""

    profile: FinancialProfile | None = None
    memory: ChatMemory | None = None


class LedgerChatResponse(ChatResponse):
    plan: BudgetPlan | None = None
    memory: ChatMemory | None = None


class ChatFocus(_Strict):
    """The model selects a validated topic and optional reference. Python writes the reply."""

    topic: Literal[
        "summary", "line", "remaining", "next_steps", "missing_information", "clarify", "off_topic",
        "spending", "largest_category", "category_limit", "spending_comparison", "expense_trend",
        "reduce_expenses", "savings_goal", "unusual_spending", "transaction", "monthly_budget",
        "income", "upcoming_bills", "recurring_bills",
    ]
    line_index: int | None = Field(default=None, ge=0)
    target_index: int | None = Field(default=None, ge=0)
    period: Literal["current", "previous"] = "current"
    detail: Literal["concise", "breakdown"] = "concise"
