import json
from decimal import Decimal

from model.budget_chat import answer_ledger_chat
from model.schemas import ChatFocus, LedgerChatRequest


RECORDS = {
    "coverage_start": "2026-10-01",
    "as_of": "2026-11-19",
    "transactions": [
        {"posted_on": "2026-10-08", "name": "Groceries", "category": "food", "amount": 3000},
        {"posted_on": "2026-10-25", "name": "Snacks", "category": "food", "amount": 300},
        {"posted_on": "2026-11-03", "name": "Groceries", "category": "food", "amount": 5000},
        {"posted_on": "2026-11-10", "name": "Lunch", "category": "food", "amount": 600},
        {"posted_on": "2026-11-11", "name": "Water bill", "category": "utilities", "amount": 700},
    ],
    "monthly_budget": 5800,
    "category_limits": {"food": 5200},
    "upcoming_bills": [
        {"name": "Electricity", "amount": 800, "due_on": "2026-11-20", "recurring": True},
        {"name": "Repairs", "amount": 430, "due_on": "2026-11-21"},
    ],
    "income_total": 21000,
    "previous_income_total": 23000,
    "savings_goal": {"name": "Emergency fund", "target_amount": 10000, "current_amount": 4800},
}


def ask(message, choose_focus, memory=None, records=RECORDS):
    request = LedgerChatRequest(message=message, records=records, memory=memory)
    return answer_ledger_chat(request, "PHP", choose_focus)


def no_inference(_messages):
    raise AssertionError("The explicit question already identifies the relevant records")


def test_category_period_and_difference_followups_use_same_verified_reference():
    request = LedgerChatRequest(message="How much did I spend on food this month?", records=RECORDS, profile={
        "incomes": [{"name": "Salary", "monthly_amount": 21000}],
        "expenses": [{"name": "Food", "category": "food", "monthly_amount": 3000}],
    })
    first = answer_ledger_chat(request, "PHP", no_inference)
    assert first.plan is not None
    assert "PHP 5,600.00" in first.reply
    assert first.memory.category == "food"

    second = ask("What about last month?", no_inference, memory=first.memory)
    assert "PHP 3,300.00" in second.reply
    assert "Oct 1–31" in second.reply
    assert second.memory.period == "previous"

    final = ask("Why is there a difference?", no_inference, memory=second.memory)
    assert "PHP 2,300.00 more" in final.reply
    assert "Groceries, which increased by PHP 2,000.00" in final.reply
    assert "full prior month" in final.reply
    assert "Water bill" not in final.reply


def test_explicit_current_month_total_uses_just_that_period():
    result = ask("How much did I spend this month?", no_inference)
    assert "PHP 6,300.00" in result.reply
    assert "Oct" not in result.reply


def test_income_and_expenses_use_one_month_and_keep_the_followup_topic():
    current = ask("check my current expenses and income for the month of november", no_inference)
    assert "PHP 21,000.00 in income and PHP 6,300.00 in expenses" in current.reply
    assert "Nov 1–19, 2026" in current.reply
    assert current.memory.topic == "income_and_expenses"

    previous = ask("What about last month?", no_inference, memory=current.memory)
    assert "PHP 23,000.00 in income and PHP 3,300.00 in expenses" in previous.reply
    assert "Oct 1–31, 2026" in previous.reply

    named_previous = ask("Show my income and expenses for October 2026", no_inference)
    assert named_previous.reply == previous.reply


def test_combined_totals_handle_missing_records_and_an_unsupported_month():
    question = "Show my income and expenses this month"
    expenses_only = ask(question, no_inference, records={**RECORDS, "income_total": None})
    assert "PHP 6,300.00 in expenses" in expenses_only.reply
    assert "no recorded income" in expenses_only.reply
    income_only = ask(question, no_inference, records={**RECORDS, "transactions": []})
    assert "PHP 21,000.00 in income" in income_only.reply
    assert "no recorded expenses" in income_only.reply
    empty = ask(question, no_inference, records={**RECORDS, "income_total": None, "transactions": []})
    assert "no recorded income or expense entries" in empty.reply

    old_year = ask("Show my expenses and income in November 2025", no_inference)
    assert "this month or last month" in old_year.reply


def test_reference_requires_same_conversation_context():
    reply = ask("What about last month?", no_inference)
    assert "Which spending category" in reply.reply
    assert reply.memory is None


def test_budget_status_uses_stored_overall_limit_without_needing_income():
    reply = ask("Am I exceeding my monthly budget?", no_inference)
    assert "PHP 6,300.00" in reply.reply
    assert "PHP 500.00 over your PHP 5,800.00 monthly budget" in reply.reply
    assert reply.plan is None


def test_saved_category_limits_do_not_imply_an_overall_limit():
    reply = ask("Am I exceeding my monthly budget?", no_inference, records={**RECORDS, "monthly_budget": None})
    assert "PHP 6,300.00" in reply.reply
    assert "PHP 5,600.00" in reply.reply
    assert "PHP 400.00 over your PHP 5,200.00 combined limits for those categories" in reply.reply
    assert "Set an overall monthly budget" in reply.reply


def test_missing_category_distinguishes_absent_transactions_from_uncovered_period():
    missing = ask("How much did I spend on health this month?", no_inference)
    assert "no recorded health transactions" in missing.reply
    assert "PHP 0.00" not in missing.reply

    uncovered = ask("How much did I spend on health this month?", no_inference, records={
        **RECORDS, "coverage_start": "2026-11-06", "transactions": RECORDS["transactions"][-2:],
    })
    assert "covering the requested month" in uncovered.reply


def test_qwen_selects_a_topic_from_compact_verified_aggregates():
    def route(messages):
        context = json.loads(messages[-1]["content"])
        verified = context["verified_context"]
        assert Decimal(verified["recorded_spending"]) == 6300
        assert verified["largest_categories"][0]["category"] == "food"
        assert Decimal(verified["largest_categories"][0]["total"]) == 5600
        assert "Water bill" not in str(verified)
        assert "upcoming_bills" not in verified
        assert "recorded_income" not in verified
        return ChatFocus(topic="largest_category")

    reply = ask("Which category consumes most of my budget?", route)
    assert "Food was your largest" in reply.reply
    assert "88.9% of recorded spending" in reply.reply


def test_income_bill_and_goal_use_separate_recorded_fields():
    income = ask("How much income did I record last month?", no_inference)
    assert "PHP 23,000.00" in income.reply

    recurring = ask("Which of my bills repeat monthly?", no_inference)
    assert "Electricity is due Nov 20, 2026 for PHP 800.00" in recurring.reply
    assert "repeats monthly" in recurring.reply
    assert "Repairs" not in recurring.reply

    savings = ask("How is that savings goal going?", lambda _: ChatFocus(topic="savings_goal"))
    assert "PHP 4,800.00" in savings.reply
    assert "PHP 5,200.00 more" in savings.reply


def test_budget_followup_uses_the_prior_months_own_limits():
    records = {**RECORDS, "previous_monthly_budget": 3000, "previous_category_limits": {"food": 3200}}
    current = ask("Am I exceeding my monthly budget?", no_inference, records=records)
    previous = ask("What about last month?", no_inference, memory=current.memory, records=records)
    assert "Oct 1–31" in previous.reply
    assert "PHP 300.00 over your PHP 3,000.00 monthly budget" in previous.reply

    category = ask("Did I exceed my food budget last month?", lambda _: ChatFocus(topic="category_limit"),
                   records=records)
    assert "PHP 100.00 over your PHP 3,200.00 monthly limit" in category.reply

    missing = ask("What about last month?", no_inference, memory=current.memory)
    assert "budget saved for that month" in missing.reply
    assert "PHP 5,800.00" not in missing.reply
