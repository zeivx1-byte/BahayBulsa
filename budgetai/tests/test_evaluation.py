"""Phase 6: the evaluation scorer, the evaluation case files and the runner.

No real model is used: a fake client returns fixed replies.
"""
import json
from types import SimpleNamespace

import pytest

import config
from budgeting_engine.planner import make_plan
from evaluation import cases as case_files
from evaluation import evaluate_model as ev
from evaluation.scoring import find_numbers, score_entry
from model.ai_service import ModelRateLimitError, ModelReply
from model.budgeting_ai import BudgetAI
from model.schemas import FinancialProfile

SANTOS = case_files.SANTOS
REYES = case_files.REYES
QUIET = SimpleNamespace(chat=None)


def entry_for(profile, advice, json_first_try=True):
    ai = BudgetAI(client=QUIET)
    prof = FinancialProfile.model_validate(profile)
    plan = make_plan(prof)
    return {"profile": profile, "plan": plan.model_dump(mode="json"), "prompt": ai._build_request(prof, plan),
            "raw_advice": advice, "final_advice": advice, "json_first_try": json_first_try}


def advice(summary="", savings="", warnings=(), questions=(), flags=()):
    return {"summary": summary, "savings_advice": savings, "spending_flags": list(flags),
            "warnings": list(warnings), "missing_information": list(questions)}


def rules(score):
    return {c["rule"] for c in score["contradictions"]}


# ------------------------------------------------------------------ numbers
def test_numbers_are_classified_by_unit():
    kinds = [(n["value"], n["kind"]) for n in find_numbers(
        "PHP 4,600.00, ₱800, P1,000, 46%, 0.25 months, 3 people, a 1-month fund, 1,000 PHP and 2 debts")]
    assert kinds == [(4600, "money"), (800, "money"), (1000, "money"), (46, "percent"), (0.25, "months"),
                     (3, "count"), (1, "months"), (1000, "money"), (2, "count")]


# ------------------------------------------------------------------ the named regressions
def test_santos_extra_saving_without_a_source_is_flagged():
    text = ("With only about 0.25 months of expenses left in your emergency fund, consider adding an extra "
            "1,000 PHP monthly to build the full 1-month safety net faster.")
    score = score_entry(entry_for(SANTOS, advice("Your budget is stable.", text)))["model"]
    assert "unplanned_extra_saving" in rules(score)
    assert [a["value"] for a in score["invented_amounts"]] == [1000]
    assert not score["pass"]


def test_santos_saving_with_a_named_source_is_not_flagged_as_unplanned():
    text = "You could save an extra 1000 by cutting the Online shopping budget."
    score = score_entry(entry_for(SANTOS, advice("Your budget is stable.", text)))["model"]
    assert "unplanned_extra_saving" not in rules(score)


def test_santos_weak_emergency_fund_called_healthy_is_flagged():
    score = score_entry(entry_for(SANTOS, advice("You have a healthy emergency fund covering less than a month.")))
    assert "weak_emergency_fund_praised" in rules(score["model"])


def test_unavailable_emergency_fund_coverage_is_not_scored_as_zero():
    profile = {"incomes": [{"name": "Salary", "monthly_amount": 20000}]}
    entry = entry_for(profile, advice("You have a healthy emergency fund covering 0 months."))
    assert entry["plan"]["facts"]["emergency_fund_months"] is None
    result = score_entry(entry)["model"]
    assert "weak_emergency_fund_praised" not in rules(result)
    assert [item["value"] for item in result["unsupported_months"]] == [0]


def test_reyes_claim_that_800_was_set_aside_is_flagged():
    text = ("Your income of PHP 16,000 is 4,600 short. You have already set aside PHP 800 for "
            "non-essential spending.")
    score = score_entry(entry_for(REYES, advice(text)))["model"]
    hits = [c for c in score["contradictions"] if c["rule"] == "set_aside_not_in_plan"]
    assert hits and "800.00" in hits[0]["detail"]
    assert score["factual_ok"]           # 800 was in the prompt; the claim about it is what is wrong


def test_a_faithful_santos_explanation_passes_every_check():
    good = advice(
        "Your budget is stable: after required spending of 39599 you have 10401 left. Your emergency fund "
        "covers only 0.25 months, and the credit card charges 36% a year.",
        "This month put 3120.3 into the emergency fund and pay 3120.3 extra on the credit card.",
        warnings=["Your emergency fund covers only 0.25 months of required spending."])
    score = score_entry(entry_for(SANTOS, good))["model"]
    assert score["pass"], score


def test_a_faithful_reyes_explanation_passes_and_states_the_shortfall():
    good = advice("Your income of 16000 is 4600 short of required spending, so essentials are funded at 91%.",
                  "Ask the lender for a longer term.", warnings=["Income is 4600 short of required spending."])
    score = score_entry(entry_for(REYES, good))["model"]
    assert score["pass"], score
    bad = advice("Your budget is stable and all your needs are covered.", "")
    score = score_entry(entry_for(REYES, bad))["model"]
    assert "deficit_denied" in rules(score) and "deficit_shortfall" in score["critical_missing"]


# ------------------------------------------------------------------ Phase 5 cases
def regression(case_id):
    return next(c for c in case_files.REGRESSION_CASES if c["id"] == case_id)["profile"]


def test_held_debt_must_be_explained_and_not_paid():
    profile = regression("R-HELD-DEBT")
    paid = score_entry(entry_for(profile, advice("Pay the 500 minimum on your Store credit this month.")))["model"]
    assert "held_debt_paid" in rules(paid) and "held_debt:Store credit" in paid["critical_missing"]
    good = advice("Your Store credit payment is not in the budget until you confirm the balance and minimum.")
    assert score_entry(entry_for(profile, good))["model"]["critical_ok"]


def test_partial_plan_must_be_called_a_partial_plan():
    profile = regression("R-PARTIAL")
    bad = score_entry(entry_for(profile, advice("No budget can be made yet.")))["model"]
    assert "partial_called_no_budget" in rules(bad) and "partial_plan" in bad["critical_missing"]
    good = advice("This is a partial plan: only the 1500 loan payment is budgeted; 23500 waits for your expenses.")
    assert score_entry(entry_for(profile, good))["model"]["critical_ok"]


def test_irregular_income_without_a_low_month():
    profile = regression("R-IRREGULAR-STATED")
    bad = score_entry(entry_for(profile, advice("The budget is based on your lowest month of income.")))["model"]
    assert "false_low_month" in rules(bad)
    good = advice("The budget used your stated monthly amount of 30000; a low month could leave less.")
    assert score_entry(entry_for(profile, good))["model"]["critical_ok"]


def test_missing_information_questions_are_scored():
    profile = {"incomes": [{"name": "Salary", "monthly_amount": 20000}],
               "expenses": [{"name": "Rent", "category": "housing", "monthly_amount": 6000}]}
    asked = score_entry(entry_for(profile, advice("ok", questions=["How many people live in your household?"])))
    assert asked["model"]["missing_ok"]
    silent = score_entry(entry_for(profile, advice("ok")))
    assert not silent["model"]["missing_ok"]
    extra = score_entry(entry_for(SANTOS, advice("ok", questions=["What is your favorite food?"])))
    assert not extra["model"]["missing_ok"]


# ------------------------------------------------------------------ evaluation case files
def test_regression_and_challenge_files_match_their_definitions_and_pass_every_check():
    for path, (set_name, definitions) in case_files.all_definitions().items():
        records = case_files.load(path)
        assert records == [json.loads(json.dumps(case_files.build_record(c, set_name))) for c in definitions]
        assert case_files.check_records(records) == []


def test_evaluation_cases_stay_out_of_training_data_and_prompts():
    def key(profile):
        normalized = FinancialProfile.model_validate(profile).model_dump(mode="json")
        return json.dumps(normalized, sort_keys=True)

    def profiles(records):
        return {key(rec["profile"]) for rec in records}

    training = profiles(case_files.load(config.settings.dataset_dir / "budgeting_examples.jsonl"))
    heldout = profiles(case_files.load(config.settings.dataset_dir / "test_scenarios.jsonl"))
    regression = case_files.load(case_files.REGRESSION_FILE)
    challenge = case_files.load(case_files.CHALLENGE_FILE)
    assert 20 <= len(challenge) <= 30
    assert len(profiles(challenge)) == len(challenge)
    assert profiles(challenge).isdisjoint(training | heldout | profiles(regression))
    instructions = (config.PROJECT_ROOT / "model" / "instructions.txt").read_text(encoding="utf-8")
    generator = (config.PROJECT_ROOT / "dataset" / "generate_dataset.py").read_text(encoding="utf-8")
    for path, records in ((case_files.REGRESSION_FILE, regression), (case_files.CHALLENGE_FILE, challenge)):
        for rec in records:
            assert key(rec["profile"]) not in training, rec["id"]
            assert rec["id"] not in instructions
        assert path.name not in generator


# ------------------------------------------------------------------ the runner, with a fake model
class FakeClient:
    """Returns a faithful reply, or invalid JSON for chosen prompts."""

    def __init__(self, invalid_when=None, fail=None):
        self.invalid_when, self.fail = invalid_when, fail

    def chat(self, *, messages, **_kwargs):
        if self.fail:
            raise self.fail
        prompt = messages[1]["content"]
        if self.invalid_when and self.invalid_when in prompt:
            content = '{"summary": "cut off'
        else:
            summary = "This is a partial plan." if "Partial plan" in prompt else "Here is your budget."
            content = json.dumps(advice(summary, "Follow the steps."))
        return ModelReply(content, input_tokens=900, output_tokens=100, total_seconds=3,
                          prompt_seconds=0.5, completion_seconds=2)


@pytest.fixture
def quiet_run_info(monkeypatch):
    monkeypatch.setattr(ev, "git_info", lambda: {"commit": "test", "branch": "main", "dirty_files": [], "clean": True})


def run(tmp_path, client, **kwargs):
    recorder = ev.RecordingClient(client)
    ai = BudgetAI(client=recorder)
    return ev.run_set("regression", "test", ai, recorder, tmp_path, **kwargs)


def test_runner_saves_replies_scores_and_a_report(tmp_path, quiet_run_info):
    run_dir = run(tmp_path, FakeClient(invalid_when="Online lending app"))  # Reyes gets invalid JSON
    summary = json.loads((run_dir / "summary.json").read_text(encoding="utf-8"))
    meta = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
    assert summary["cases_ran"] == 5 and summary["planner"]["ok"] == 5
    assert summary["json"]["within_attempts"]["failed_ids"] == ["R-REYES"]
    assert summary["json"]["retries"] == 2
    assert summary["timing"]["output_tokens_mean"] == 100
    assert summary["timing"]["tokens_per_s_mean"] == 50
    assert meta["settings"]["temperature"] == 0 and meta["model"]["tag"] == config.settings.model_name
    assert meta["model"]["provider"] == "groq"
    entries = case_files.load(run_dir / "cases.jsonl")
    reyes = next(e for e in entries if e["id"] == "R-REYES")
    assert reyes["status"] == "invalid_json" and reyes["attempts"] == 3 and reyes["raw_advice"] is None
    partial = next(e for e in entries if e["id"] == "R-PARTIAL")
    assert partial["scores"]["model"]["critical_ok"] and "Partial plan" in partial["prompt"]
    assert (run_dir / "report.md").read_text(encoding="utf-8").startswith("# BudgetAI evaluation: regression")


def test_runner_stops_after_repeated_runtime_errors_and_reports_how_many_ran(tmp_path, quiet_run_info):
    run_dir = run(tmp_path, FakeClient(fail=ConnectionError("refused")))
    summary = json.loads((run_dir / "summary.json").read_text(encoding="utf-8"))
    assert summary["interrupted"] and summary["cases_ran"] == 0
    assert summary["runtime_errors"] == ["R-SANTOS", "R-REYES", "R-HELD-DEBT"]
    assert summary["cases_planned"] == 5


def test_an_interrupted_run_can_be_resumed_and_rescored(tmp_path, quiet_run_info):
    run_dir = run(tmp_path, FakeClient(), limit=2)
    first = json.loads((run_dir / "summary.json").read_text(encoding="utf-8"))
    assert first["cases_ran"] == 2
    recorder = ev.RecordingClient(FakeClient())
    ev.run_set("regression", "test", BudgetAI(client=recorder), recorder, tmp_path, resume_dir=run_dir)
    resumed = json.loads((run_dir / "summary.json").read_text(encoding="utf-8"))
    assert resumed["cases_ran"] == 5
    assert ev.rescore(run_dir)["model"] == resumed["model"]


def test_rate_limited_case_remains_pending_for_resume(tmp_path, quiet_run_info):
    class RateLimitedClient(FakeClient):
        def chat(self, **kwargs):
            if "Online lending app" in kwargs["messages"][1]["content"]:
                raise ModelRateLimitError("Quota reached", retry_after="30")
            return super().chat(**kwargs)

    with pytest.raises(ModelRateLimitError):
        run(tmp_path, RateLimitedClient())

    run_dir = tmp_path / "regression"
    meta = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
    assert meta["interrupted"] and meta["interrupt_reason"] == "Quota reached"
    assert [case["id"] for case in case_files.load(run_dir / "cases.jsonl")] == ["R-SANTOS"]

    run(tmp_path, FakeClient(), resume_dir=run_dir)
    summary = json.loads((run_dir / "summary.json").read_text(encoding="utf-8"))
    assert not summary["interrupted"] and summary["cases_ran"] == 5


def test_pythons_own_non_essential_cuts_count_as_valid_flags():
    santos = entry_for(SANTOS, advice("ok"))
    plan_lines = [l for l in santos["plan"]["budget"] if l["priority"] == 6]
    santos["final_advice"]["spending_flags"] = [
        {"expense_name": l["name"], "current_amount": l["needed_amount"], "suggested_amount": l["amount"],
         "reason": l["reason"]} for l in plan_lines]
    assert score_entry(santos)["system"]["flags_ok"]
    santos["final_advice"]["spending_flags"][0]["suggested_amount"] = 1000
    assert not score_entry(santos)["system"]["flags_ok"]
