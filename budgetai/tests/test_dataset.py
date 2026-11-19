"""Tests for the dataset: the files pass validation, the generator is repeatable,
and the validator really catches mistakes (each test breaks a budget on purpose)."""
import copy
import json

import pytest

from dataset import generate_dataset as gen
from dataset import validate_dataset as val


@pytest.fixture(scope="module")
def records():
    return val.load(val.TRAIN_FILE) + val.load(val.TEST_FILE)


def pick(records, situation, with_rule_lines=False):
    for rec in records:
        if rec["expected"]["facts"]["situation"] == situation and rec["scenario_type"] != "golden":
            if not with_rule_lines or any(l["source"] == "rule" for l in rec["expected"]["budget"]):
                return copy.deepcopy(rec)
    raise AssertionError(f"no {situation} scenario found")


def test_dataset_files_pass_validation():
    report = val.validate()
    assert report["problems"] == []
    assert report["checked"] == 412 and report["golden"] == 10


def test_generator_is_repeatable_and_matches_the_files(records):
    train, test = gen.generate()
    assert json.dumps(train + test, sort_keys=True) == json.dumps(records, sort_keys=True)


def test_validator_catches_a_budget_above_income(records):
    rec = pick(records, "stable")
    rec["expected"]["budget"][0]["amount"] += 5000
    rec["expected"]["total_allocated"] += 5000
    assert any("above planning income" in p for p in val.independent_checks(rec))


def test_validator_catches_unassigned_money(records):
    rec = pick(records, "stable", with_rule_lines=True)
    line = next(l for l in rec["expected"]["budget"] if l["source"] == "rule" and l["amount"] > 100)
    line["amount"] -= 100
    rec["expected"]["total_allocated"] -= 100
    assert any("left unassigned" in p for p in val.independent_checks(rec))


def test_validator_catches_wants_during_a_deficit(records):
    rec = pick(records, "deficit")
    rec["expected"]["budget"].append({"name": "Dining out", "category": "dining_out", "priority": 6, "amount": 100,
                                      "needed_amount": 500, "source": "rule", "reason": "x"})
    assert any("during a deficit" in p for p in val.independent_checks(rec))


def test_validator_catches_a_missing_required_line(records):
    rec = pick(records, "stable")
    rec["expected"]["budget"] = [l for l in rec["expected"]["budget"] if l["priority"] != 1][:]
    assert any("required lines do not match" in p for p in val.independent_checks(rec))


def test_validator_catches_a_wrong_hand_checked_amount(records):
    rec = copy.deepcopy(next(r for r in records if r["id"] == "BA-G01"))
    rec["hand_checked"]["Emergency fund"] = 2751
    assert any("hand-checked 2751.00" in p for p in val.golden_checks(rec))


def test_validator_catches_an_invented_amount_in_the_steps(records):
    rec = pick(records, "stable", with_rule_lines=True)
    rec["expected"]["action_steps"].append("Save PHP 123,456.78 this month.")
    assert any("PHP 123,456.78" in p for p in val.independent_checks(rec))


# ------------------------------------------------------------------ Phase 5 checks
def plan_record(profile):
    """A dataset-style record built from a profile, as the generator would write it."""
    from budgeting_engine.planner import make_plan
    expected = make_plan(profile).model_dump(mode="json", exclude={"rules_version"})
    return {"id": "BA-TEST", "profile": profile, "expected": expected}


HELD = {"incomes": [{"name": "Salary", "monthly_amount": 30000}],
        "expenses": [{"name": "Rent", "category": "housing", "monthly_amount": 10000},
                     {"name": "Food", "category": "food", "monthly_amount": 8000}],
        "debts": [{"name": "Store credit", "balance": 100, "minimum_payment": 500, "annual_interest_rate_percent": 24}],
        "household_size": 2}
PARTIAL = {"incomes": [{"name": "Salary", "monthly_amount": 25000}],
           "debts": [{"name": "SSS salary loan", "balance": 20000, "minimum_payment": 1500,
                      "annual_interest_rate_percent": 10}],
           "household_size": 3}


def test_planner_output_for_phase5_cases_passes_the_independent_checks():
    for profile in (HELD, PARTIAL):
        assert val.independent_checks(plan_record(profile)) == []


def test_validator_catches_amounts_that_are_not_whole_centavos(records):
    rec = pick(records, "stable", with_rule_lines=True)
    rec["expected"]["budget"][0]["amount"] += 0.004
    rec["expected"]["budget"][1]["amount"] -= 0.004
    assert any("whole number of centavos" in p for p in val.independent_checks(rec))


def test_validator_catches_displayed_lines_that_do_not_add_up(records):
    rec = pick(records, "stable", with_rule_lines=True)
    rec["expected"]["total_allocated"] += 0.01
    rec["expected"]["unallocated"] -= 0.01
    assert any("the lines shown add up to" in p for p in val.independent_checks(rec))


def test_validator_catches_a_budgeted_debt_on_hold():
    rec = plan_record(HELD)
    rec["expected"]["budget"].append({"name": "Store credit (minimum payment)", "category": "debt_payment",
                                      "priority": 2, "amount": 500.0, "needed_amount": 500.0,
                                      "source": "required", "reason": "Minimum payment."})
    rec["expected"]["rule_notes"] = []
    problems = val.independent_checks(rec)
    assert any("required lines do not match" in p for p in problems)
    assert any("no note says it is not budgeted" in p for p in problems)


def test_validator_catches_an_unlabelled_partial_plan():
    rec = plan_record(PARTIAL)
    rec["expected"]["rule_notes"] = ["No expenses are listed, so the income is left unallocated until they are added."]
    rec["expected"]["budget"][0]["reason"] = "The minimum payment avoids penalties and protects your credit."
    problems = val.independent_checks(rec)
    assert any("without a 'Partial plan' note" in p for p in problems)
    assert any("does not say it is part of a partial plan" in p for p in problems)


def test_validator_catches_irregular_income_without_the_stated_amount_note(records):
    rec = next(copy.deepcopy(r) for r in records
               if any(not i.get("regular", True) and i.get("minimum_monthly_amount") is None for i in r["profile"]["incomes"]))
    rec["expected"]["rule_notes"] = [n for n in rec["expected"]["rule_notes"] if "stated monthly amount" not in n]
    assert any("no note says the stated monthly amount was used" in p for p in val.independent_checks(rec))


def test_validator_requires_profiles_stored_in_whole_centavos(records):
    rec = pick(records, "stable")
    rec["profile"]["expenses"][0]["monthly_amount"] += 0.006
    assert any("profile amount is not a whole number of centavos" in p for p in val.independent_checks(rec))
