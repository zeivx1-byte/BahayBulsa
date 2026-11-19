"""Tests for missing information and AI advice validation."""
from budgeting_engine.validation import adjustable_expenses, clean_ai_advice, find_missing_data
from model.schemas import AIBudgetAdvice, BudgetLine, FinancialProfile, SpendingFlag


def missing(**profile):
    data = {"incomes": [{"name": "Salary", "monthly_amount": 25000}], **profile}
    return find_missing_data(FinancialProfile.model_validate(data))


def test_complete_profile_has_nothing_missing():
    assert missing(
        expenses=[{"name": "Rent", "category": "housing", "monthly_amount": 7000}],
        debts=[{"name": "Loan", "balance": 1000, "minimum_payment": 100, "annual_interest_rate_percent": 12}],
        savings_goals=[{"name": "Trip", "target_amount": 10000, "months_left": 5}],
        household_size=2,
    ) == []


def test_each_gap_is_reported():
    found = missing(
        incomes=[{"name": "Freelance", "monthly_amount": 20000, "regular": False}],
        debts=[{"name": "Loan app", "balance": 5000, "minimum_payment": 500}],
        savings_goals=[{"name": "Trip", "target_amount": 10000}],
    )
    text = " ".join(found)
    assert "No expenses" in text
    assert "household size" in text
    assert "'Freelance' is irregular" in text
    assert "interest rate of 'Loan app'" in text
    assert "deadline of the savings goal 'Trip'" in text
    assert len(found) == 5


def test_given_information_is_never_reported_missing():
    found = missing(
        expenses=[{"name": "Rent", "category": "housing", "monthly_amount": 7000}],
        debts=[{"name": "Loan app", "balance": 8000, "minimum_payment": 1600, "annual_interest_rate_percent": 120}],
        household_size=3,
    )
    assert found == []   # the balance and rate are given, so nothing is asked


def proposal(questions):
    return AIBudgetAdvice(summary="", savings_advice="", spending_flags=[], warnings=[],
                          missing_information=questions)


def test_python_questions_are_used_when_the_ai_asks_none():
    profile = FinancialProfile.model_validate({"incomes": [{"name": "Salary", "monthly_amount": 25000}]})
    missing = find_missing_data(profile)
    cleaned, _ = clean_ai_advice(proposal([]), profile, [], missing)
    assert cleaned.missing_information == missing


def test_extra_ai_questions_are_trimmed():
    profile = FinancialProfile.model_validate({"incomes": [{"name": "Salary", "monthly_amount": 25000}],
                                               "expenses": [{"name": "Rent", "category": "housing", "monthly_amount": 7000}]})
    missing = find_missing_data(profile)            # only household size
    cleaned, _ = clean_ai_advice(proposal(["How many people live with you?", "Do you own a car?"]), profile, [], missing)
    assert cleaned.missing_information == ["How many people live with you?"]


def expense_profile(expenses):
    return FinancialProfile.model_validate({"incomes": [{"name": "Salary", "monthly_amount": 25000}],
                                            "expenses": expenses})


def flag(name, amount, reason="Compare prices before buying."):
    return SpendingFlag(expense_name=name, current_amount=0, suggested_amount=amount, reason=reason)


def test_only_variable_required_costs_with_unambiguous_names_are_adjustable():
    profile = expense_profile([
        {"name": "Groceries", "category": "food", "monthly_amount": 1600},
        {"name": "Water", "category": "utilities", "monthly_amount": 0.02},
        {"name": "Bus fare", "category": "transportation", "monthly_amount": 500},
        {"name": "School installments", "category": "education", "monthly_amount": 1600, "fixed": False},
        {"name": "Contract fare", "category": "transportation", "monthly_amount": 300, "fixed": True},
        {"name": "Party groceries", "category": "food", "monthly_amount": 900, "kind": "non_essential"},
        {"name": "Rounding test", "category": "utilities", "monthly_amount": 0.01},
        {"name": "Electricity", "category": "utilities", "monthly_amount": 1200},
        {"name": "ELECTRICITY", "category": "other", "monthly_amount": 1},
    ])

    assert [expense.name for expense in adjustable_expenses(profile)] == ["Groceries", "Water", "Bus fare"]


def test_flags_use_unambiguous_full_names_and_keep_the_first_valid_proposal():
    profile = expense_profile([
        {"name": "Groceries", "category": "food", "monthly_amount": 1600},
        {"name": "Groceries Delivery", "category": "food", "monthly_amount": 1200},
    ])
    advice = proposal([]).model_copy(update={"spending_flags": [
        flag("", 1), flag("Grocery", 500), flag("  groceries delivery  ", 900), flag("Groceries Delivery", 800),
    ]})

    cleaned, notes = clean_ai_advice(advice, profile, [], [])

    assert cleaned.spending_flags == [SpendingFlag(expense_name="Groceries Delivery", current_amount=1200,
                                                  suggested_amount=900, reason="Compare prices before buying.")]
    assert notes[0].startswith("Removed 3 AI spending flag(s)")


def test_rounded_suggested_amount_is_positive_and_below_the_current_amount():
    profile = expense_profile([{"name": "Water", "category": "utilities", "monthly_amount": 0.02}])
    advice = proposal([]).model_copy(update={"spending_flags": [
        flag("Water", 0.004), flag("Water", 0.015), flag("Water", 0.005),
    ]})

    cleaned, notes = clean_ai_advice(advice, profile, [], [])

    assert len(cleaned.spending_flags) == 1
    assert cleaned.spending_flags[0].suggested_amount == 0.01
    assert notes[0].startswith("Removed 2 AI spending flag(s)")


def test_quantitative_or_empty_reasons_are_replaced_and_python_flags_are_preserved():
    profile = expense_profile([
        {"name": "Groceries", "category": "food", "monthly_amount": 1600},
        {"name": "Water", "category": "utilities", "monthly_amount": 300},
        {"name": "Bus fare", "category": "transportation", "monthly_amount": 1000},
    ])
    advice = proposal([]).model_copy(update={"spending_flags": [
        flag("Groceries", 800, "You are paying 75% and would save PHP 400."),
        flag("Water", 250, "You would save a few hundred."),
        flag("Bus fare", 800, " "),
    ]})
    line = BudgetLine(name="Dining out", category="dining_out", priority=6, amount=20, needed_amount=100,
                      source="rule", reason="Lowered from PHP 100 to PHP 20 in the plan.")

    cleaned, _ = clean_ai_advice(advice, profile, [line], [])

    assert [item.reason for item in cleaned.spending_flags[:3]] == [
        "Compare prices before buying.",
        "Check your bills for avoidable charges and usage.",
        "Check whether a lower-fare route fits your commute.",
    ]
    assert cleaned.spending_flags[3].reason == line.reason
