"""Project an owner-scoped website ledger into the verified BudgetAI chat input."""

from calendar import monthrange
from datetime import date, timedelta
from decimal import Decimal
import re
from typing import get_args

from model.schemas import ChatMemory, ChatTurn, ExpenseCategory, LedgerChatRequest


_CATEGORIES = frozenset(get_args(ExpenseCategory))
_CATEGORY_ALIASES = {
    "rent": "housing", "mortgage": "housing", "groceries": "food", "grocery": "food",
    "electricity": "utilities", "electric": "utilities", "water": "utilities", "internet": "utilities",
    "fuel": "transportation", "fare": "transportation", "transport": "transportation",
    "medicine": "health", "tuition": "education", "restaurant": "dining_out", "takeout": "dining_out",
}
_RECURRING_BILLS = re.compile(
    r"^(?:which|what|show|list)\b.*\b(?:recurring bills|bills (?:that )?(?:repeat monthly|recur|repeat every month))\b",
    re.IGNORECASE,
)


def prior_month_start(month_start: date) -> date:
    return (month_start - timedelta(days=1)).replace(day=1) if month_start > date.min else month_start


def build_ledger_request(
    message: str, as_of: date, rows: list[dict], bills: list[dict], preferences: dict,
    previous_preferences: dict, history: list[dict], memory: dict | None,
) -> LedgerChatRequest:
    current_start = as_of.replace(day=1)
    previous_start = prior_month_start(current_start)
    coverage = current_start
    current_income = previous_income = Decimal(0)
    expenses = []
    monthly_expenses: dict[tuple[str, str], tuple[str, Decimal]] = {}

    for row in rows:
        day = date.fromisoformat(row["date"])
        if not previous_start <= day <= as_of:
            continue
        amount = Decimal(str(row["amount"]))
        current = day >= current_start
        if not current:
            coverage = previous_start
        if row["type"] == "income":
            if current:
                current_income += amount
            else:
                previous_income += amount
            continue
        name = row["category"][:80].strip()
        key = "_".join(name.casefold().replace("-", " ").replace("_", " ").split())
        category = key if key in _CATEGORIES else _CATEGORY_ALIASES.get(key, "other")
        expenses.append({"posted_on": day, "name": name, "category": category, "amount": amount})
        if current:
            match = category, name.casefold()
            label, total = monthly_expenses.get(match, (name, Decimal(0)))
            monthly_expenses[match] = label, total + amount

    goal = preferences.get("savings_goal")
    profile = None
    if current_income and len(monthly_expenses) <= 100:
        profile = {
            "incomes": [{"name": "Income recorded this month", "monthly_amount": current_income, "regular": False}],
            "expenses": [{"name": name, "category": category, "monthly_amount": amount}
                         for (category, _), (name, amount) in monthly_expenses.items()],
            "savings_goals": [goal] if goal else [],
        }
    return LedgerChatRequest.model_validate({
        "message": message,
        "history": [ChatTurn.model_validate(turn) for turn in history[-8:]],
        "memory": ChatMemory.model_validate(memory) if memory else None,
        "profile": profile,
        "records": {
            "as_of": as_of, "coverage_start": coverage, "transactions": expenses,
            "income_total": current_income or None, "previous_income_total": previous_income or None,
            "monthly_budget": preferences.get("monthly_budget"),
            "category_limits": preferences.get("category_limits", {}),
            "previous_monthly_budget": previous_preferences.get("monthly_budget"),
            "previous_category_limits": previous_preferences.get("category_limits", {}),
            "savings_goal": goal,
            "upcoming_bills": _select_bills(message, as_of, bills),
        },
    })


def _select_bills(message: str, as_of: date, bills: list[dict]) -> list[dict]:
    recurring_only = bool(_RECURRING_BILLS.search(message))
    upcoming = []
    for row in bills:
        recurring = row.get("recurring", False)
        if recurring_only and not recurring:
            continue
        day = _upcoming_date(row, as_of)
        if day is not None:
            upcoming.append({"name": row["name"][:80].strip(), "due_on": day,
                             "amount": row["amount"], "recurring": recurring})

    def priority(bill: dict):
        matching = re.search(rf"(?<!\w){re.escape(bill['name'])}(?!\w)", message, re.IGNORECASE)
        return not matching, bill["due_on"]

    return sorted(upcoming, key=priority)[:500]


def _upcoming_date(row: dict, as_of: date) -> date | None:
    due = date.fromisoformat(row["due_date"])
    if due >= as_of:
        return due
    if not row.get("recurring"):
        return None
    year, month = as_of.year, as_of.month
    candidate = date(year, month, min(due.day, monthrange(year, month)[1]))
    if candidate >= as_of:
        return candidate
    year, month = (year + 1, 1) if month == 12 else (year, month + 1)
    return date(year, month, min(due.day, monthrange(year, month)[1]))
