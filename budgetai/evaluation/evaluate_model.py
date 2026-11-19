"""BudgetAI evaluation (Phase 6): planner correctness and model quality.

Planner correctness and model quality are measured separately:

- Planner: does Python reproduce each case's expected plan exactly, and does
  the plan pass the dataset's independent checks? (No model is involved.)
- Model: the Groq model explains each plan through BudgetAI. Its raw
  reply (before Python's clean-up) is scored for valid JSON, factual accuracy,
  contradictions with the budget, spending flags, missing-information
  handling and critical facts (evaluation/scoring.py). The final output the
  household sees (after Python's clean-up) is scored with the same rules.

Every reply is saved, so runs can be rescored with the same rules later.
Output goes to the Git-ignored results/evaluation/ folder.

Run from the project folder with the virtual environment active:
    python -m evaluation.evaluate_model --set heldout regression --label baseline
    python -m evaluation.evaluate_model --set challenge --label baseline --limit 5
    python -m evaluation.evaluate_model --planner-only --set heldout regression challenge
    python -m evaluation.evaluate_model --resume results/evaluation/<run>/<set>
    python -m evaluation.evaluate_model --rescore results/evaluation/<run>/<set>
    python -m evaluation.evaluate_model --compare <before-set-folder> <after-set-folder>

Nothing here changes the model's weights.
"""
import argparse
import hashlib
import json
import platform
import statistics
import subprocess
import sys
import time
from dataclasses import asdict
from datetime import datetime
from importlib import metadata
from pathlib import Path

import config
from budgeting_engine.planner import make_plan
from dataset import validate_dataset as val
from evaluation import cases as case_files
from evaluation.scoring import SCORER_VERSION, score_entry
from model import budgeting_ai
from model.ai_service import BudgetAIError, GroqAIService, InvalidModelResponseError, ModelRateLimitError
from model.budgeting_ai import BudgetAI
from model.schemas import AIBudgetAdvice

ROOT = config.PROJECT_ROOT
RESULTS_DIR = ROOT / "results" / "evaluation"
SETS = {
    "heldout": ROOT / "dataset" / "test_scenarios.jsonl",
    "regression": case_files.REGRESSION_FILE,
    "challenge": case_files.CHALLENGE_FILE,
}
FINGERPRINT_FILES = [
    "model/instructions.txt", "model/ai_service.py", "model/budgeting_ai.py", "model/schemas.py",
    "budgeting_engine/allocation.py", "budgeting_engine/calculations.py", "budgeting_engine/insights.py",
    "budgeting_engine/planner.py", "budgeting_engine/validation.py",
    "evaluation/evaluate_model.py", "evaluation/scoring.py",
]
STOP_AFTER_RUNTIME_ERRORS = 3
WARMUP_PROFILE = {"incomes": [{"name": "Salary", "monthly_amount": 20000}],
                  "expenses": [{"name": "Rent", "category": "housing", "monthly_amount": 6000}]}

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(errors="replace")


# ------------------------------------------------------------------ recording the model calls
class RecordingClient:
    """Record each evaluation call's reply, timing and token counts."""

    def __init__(self, inner):
        self.inner = inner
        self.calls = []

    def chat(self, **kwargs):
        start = time.perf_counter()
        record = {"attempt": len(self.calls) + 1, "temperature": kwargs.get("temperature"),
                  "messages": kwargs.get("messages")}
        try:
            response = self.inner.chat(**kwargs)
        except Exception as exc:
            record.update(seconds=round(time.perf_counter() - start, 3), error=f"{type(exc).__name__}: {exc}",
                          finish_reason=getattr(exc, "finish_reason", None))
            self.calls.append(record)
            raise
        record["seconds"] = round(time.perf_counter() - start, 3)
        record.update(asdict(response))
        self.calls.append(record)
        return response

# ------------------------------------------------------------------ run information
def sha256(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def git_info() -> dict:
    def git(*args):
        try:
            out = subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, timeout=30)
            return out.stdout.strip() if out.returncode == 0 else None
        except (OSError, subprocess.SubprocessError):
            return None
    status = git("status", "--porcelain", "--untracked-files=all")
    return {"commit": git("rev-parse", "HEAD"), "branch": git("rev-parse", "--abbrev-ref", "HEAD"),
            "dirty_files": status.splitlines() if status else [], "clean": status == ""}


def run_settings(settings) -> dict:
    return {"temperature": settings.temperature, "retry_temperature": budgeting_ai.RETRY_TEMPERATURE,
            "max_completion_tokens": budgeting_ai.MAX_ANSWER_TOKENS,
            "max_format_attempts": budgeting_ai.MAX_FORMAT_ATTEMPTS,
            "format": "AIBudgetAdvice JSON schema", "timeout_seconds": settings.timeout_seconds,
            "currency": settings.currency}


def environment() -> dict:
    versions = {}
    for package in ("groq", "pydantic", "httpx"):
        try:
            versions[package] = metadata.version(package)
        except metadata.PackageNotFoundError:
            versions[package] = None
    return {"python": platform.python_version(), "platform": platform.platform(), "packages": versions}


# ------------------------------------------------------------------ cases
def load_set(name: str) -> list:
    path = SETS[name]
    if not path.exists():
        raise SystemExit(f"Evaluation set '{name}' not found: {path}")
    return case_files.load(path)


def planner_check(rec: dict) -> dict:
    plan = make_plan(rec["profile"])
    reproduced = plan.model_dump(mode="json", exclude={"rules_version"}) == rec["expected"] \
        and plan.rules_version == rec["rules_version"]
    independent = val.independent_checks(rec)
    hand = case_files.hand_check_problems(rec) if isinstance(rec.get("hand_checked"), dict) \
        and any(k in rec["hand_checked"] for k in ("lines", "facts", "absent_lines")) else []
    if rec.get("scenario_type") == "golden":
        hand += val.golden_checks(rec)
    return {"reproduced": reproduced, "independent_problems": independent, "hand_problems": hand,
            "ok": reproduced and not independent and not hand}


def evaluate_case(ai: BudgetAI, recorder: RecordingClient, rec: dict, set_name: str) -> dict:
    recorder.calls = []
    entry = {"id": rec["id"], "set": set_name, "title": rec.get("title", ""),
             "scenario_type": rec.get("scenario_type"), "regression_checks": rec.get("regression_checks", []),
             "must_communicate": rec.get("must_communicate", []), "profile": rec["profile"],
             "planner": planner_check(rec)}
    plan = make_plan(rec["profile"], ai.settings.currency)
    entry["plan"] = plan.model_dump(mode="json")
    start = time.perf_counter()
    status, error, result = "ok", None, None
    try:
        result = ai.generate_budget(rec["profile"])
    except ModelRateLimitError:
        raise
    except InvalidModelResponseError as exc:
        status, error = "invalid_json", str(exc)
    except BudgetAIError as exc:
        status, error = "runtime_error", str(exc)
    entry["seconds"] = round(time.perf_counter() - start, 3)
    entry.update(status=status, error=error)

    calls = recorder.calls
    entry["prompt"] = calls[0]["messages"][1]["content"] if calls and calls[0].get("messages") else None
    entry["calls"] = [{k: v for k, v in c.items() if k != "messages"} for c in calls]
    entry["attempts"] = len(calls)
    raw = None
    for call in calls:                         # the reply BudgetAI accepted, before Python's clean-up
        try:
            raw = AIBudgetAdvice.model_validate_json(call.get("content") or "").model_dump()
            break
        except Exception:
            continue
    first = calls[0].get("content") if calls else None
    try:
        AIBudgetAdvice.model_validate_json(first or "")
        entry["json_first_try"] = True
    except Exception:
        entry["json_first_try"] = False
    entry["raw_advice"] = raw
    entry["final_advice"] = result.advice.model_dump() if result else None
    entry["final_checks"] = result.checks if result else []
    entry["final_rule_notes"] = result.rule_notes if result else []
    entry["scores"] = score_entry(entry) if status != "runtime_error" else None
    return entry


# ------------------------------------------------------------------ summarising
def _rate(passed: int, total: int) -> str:
    return f"{passed}/{total}" + (f" ({100 * passed / total:.0f}%)" if total else "")


def _metric(entries: list, side: str, key: str, applicable=None) -> dict:
    rows = [e for e in entries if e.get("scores") and e["scores"][side].get("has_advice")]
    if applicable:
        rows = [e for e in rows if applicable(e)]
    failed = [e["id"] for e in rows if not e["scores"][side][key]]
    return {"pass": len(rows) - len(failed), "of": len(rows), "failed_ids": failed}


def summarize(entries: list, meta: dict) -> dict:
    ran = [e for e in entries if e["status"] != "runtime_error"]
    planner_failed = [e["id"] for e in entries if not e["planner"]["ok"]]
    summary = {
        "cases_planned": meta["cases_planned"], "cases_ran": len(ran), "cases_saved": len(entries),
        "runtime_errors": [e["id"] for e in entries if e["status"] == "runtime_error"],
        "interrupted": meta.get("interrupted", False), "interrupt_reason": meta.get("interrupt_reason"),
        "planner": {"ok": len(entries) - len(planner_failed), "of": len(entries), "failed_ids": planner_failed},
        "json": {
            "first_try": {"pass": sum(e.get("json_first_try", False) for e in ran), "of": len(ran),
                          "failed_ids": [e["id"] for e in ran if not e.get("json_first_try")]},
            "within_attempts": {"pass": sum(e["status"] == "ok" for e in ran), "of": len(ran),
                                "failed_ids": [e["id"] for e in ran if e["status"] != "ok"]},
            "retries": sum(max(0, e["attempts"] - 1) for e in ran),
            "length_cut": [e["id"] for e in ran if any(c.get("finish_reason") == "length" for c in e["calls"])],
        },
    }
    for side in ("model", "system"):
        scored = [e for e in ran if e.get("scores") and e["scores"][side].get("has_advice")]
        block = {
            "factual_ok": _metric(ran, side, "factual_ok"),
            "contradiction_free": _metric(ran, side, "contradiction_free"),
            "flags_ok": _metric(ran, side, "flags_ok"),
            "missing_ok_when_missing": _metric(ran, side, "missing_ok",
                                               lambda e: e["scores"][side]["missing"]["applicable"]),
            "no_questions_when_complete": _metric(ran, side, "missing_ok",
                                                  lambda e: not e["scores"][side]["missing"]["applicable"]),
            "critical_ok": _metric(ran, side, "critical_ok",
                                   lambda e: bool(e["scores"][side]["critical_required"])),
            "pass": _metric(ran, side, "pass"),
        }
        rules, left_out = {}, {}
        for e in scored:
            for c in e["scores"][side]["contradictions"]:
                rules.setdefault(c["rule"], set()).add(e["id"])
            for item in e["scores"][side]["critical_missing"]:
                left_out.setdefault(item.split(":")[0], []).append(e["id"])
        block["contradictions_by_rule"] = {r: sorted(ids) for r, ids in sorted(rules.items())}
        block["critical_left_out"] = left_out
        for key in ("invented_amounts", "unsupported_percents", "unsupported_months"):
            block[key] = sum(len(e["scores"][side][key]) for e in scored)
        block["cases_with_invented_amounts"] = [e["id"] for e in scored if e["scores"][side]["invented_amounts"]]
        block["emergency_fund_warning_missing"] = [e["id"] for e in scored
                                                   if "emergency_fund_warning" in e["scores"][side]["secondary_missing"]]
        summary[side] = block

    regression = []
    for e in ran:
        if not e.get("regression_checks") and not e.get("must_communicate"):
            continue
        row = {"id": e["id"]}
        for side in ("model", "system"):
            s = e["scores"][side] if e.get("scores") else {}
            fired = {c["rule"] for c in s.get("contradictions", [])}
            row[side] = {"rules_fired": sorted(fired & set(e["regression_checks"])),
                         "left_out": [m for m in e.get("must_communicate", []) if m in s.get("critical_missing", [])]}
            row[side]["pass"] = bool(s.get("has_advice")) and not row[side]["rules_fired"] and not row[side]["left_out"]
        regression.append(row)
    summary["regression_checks"] = regression

    seconds = [e["seconds"] for e in ran]
    calls = [c for e in ran for c in e["calls"]]
    prompt_tokens = [c["input_tokens"] for c in calls if c.get("input_tokens")]
    output_tokens = [c["output_tokens"] for c in calls if c.get("output_tokens")]
    tps = [c["output_tokens"] / c["completion_seconds"] for c in calls
           if c.get("output_tokens") and c.get("completion_seconds")]
    if seconds:
        ordered = sorted(seconds)
        summary["timing"] = {
            "cases": len(seconds), "total_s": round(sum(seconds), 1), "mean_s": round(statistics.mean(seconds), 2),
            "median_s": round(statistics.median(seconds), 2),
            "p90_s": round(ordered[min(len(ordered) - 1, int(0.9 * len(ordered)))], 2), "max_s": round(max(seconds), 2),
            "calls": len(calls),
            "tokens_per_s_mean": round(statistics.mean(tps), 1) if tps else None,
            "output_tokens_mean": round(statistics.mean(output_tokens), 1) if output_tokens else None,
            "prompt_tokens_mean": round(statistics.mean(prompt_tokens), 1) if prompt_tokens else None,
        }
    return summary


def failure_reasons(e: dict, side: str) -> list:
    if e["status"] != "ok":
        return [f"{e['status']}: {e.get('error')}"]
    s = e["scores"][side]
    out = []
    if side == "model" and not s.get("json_first_try"):
        out.append(f"invalid JSON on the first try ({e['attempts']} tries)")
    out += [f"invented {a['value']:g} in {a['field']}" for a in s["invented_amounts"]]
    out += [f"unsupported {p['value']:g}%" for p in s["unsupported_percents"]]
    out += [f"unsupported {p['value']:g} months" for p in s["unsupported_months"]]
    out += [c["rule"] + (f" ({c['detail']})" if c["detail"] else "") for c in s["contradictions"]]
    out += [f"flag {f['flag']}: {f['problem']}" for f in s["flag_problems"]]
    if not s["missing_ok"]:
        out.append(f"questions: {len(s['missing']['uncovered'])} missing item(s) not asked, "
                   f"{len(s['missing']['extra'])} unneeded")
    out += [f"left out {c}" for c in s["critical_missing"]]
    return out


def write_report(run_dir: Path, meta: dict, summary: dict) -> Path:
    m, s = meta, summary
    git = m["git"]
    model = m["model"]
    model_label = f"{model['provider']} " if model.get("provider") else ""
    model_label += f"`{model['tag']}`"
    if model.get("digest"):
        model_label += f" (digest {model['digest'][:12]})"
    lines = [f"# BudgetAI evaluation: {m['set']} ({m['label']})", "",
             f"- Started {m['started_at']}, finished {m.get('finished_at')}, scorer {m['scorer_version']}",
             f"- Model {model_label}",
             f"- Settings: {json.dumps(m['settings'])}",
             f"- Git {git['commit'] or 'unknown'} on {git['branch'] or 'unknown'}; "
             + ("clean" if git["clean"] else
                f"uncommitted: {'; '.join(git['dirty_files'])}" if git["commit"] else "git not available"),
             f"- Set file {m['set_file']} (sha256 {m['set_file_sha256'][:16]}); system prompt sha256 "
             f"{m['system_prompt_sha256'][:16]}",
             f"- Cases: {s['cases_ran']} ran of {s['cases_planned']} planned"
             + (f" (INTERRUPTED: {s['interrupt_reason']})" if s["interrupted"] else "")
             + (f"; runtime errors: {', '.join(s['runtime_errors'])}" if s["runtime_errors"] else ""), "",
             "## Planner correctness (Python, no model)",
             f"- Expected plan reproduced and all checks passed: {_rate(s['planner']['ok'], s['planner']['of'])}"
             + (f"; failed: {', '.join(s['planner']['failed_ids'])}" if s["planner"]["failed_ids"] else ""), "",
             "## Model and system quality",
             "Model = the raw reply before Python's clean-up. System = what the household sees.", "",
             "| Metric | Model | System |", "|---|---|---|",
             f"| Valid JSON on the first try | {_rate(s['json']['first_try']['pass'], s['json']['first_try']['of'])} | |",
             f"| Valid JSON within {m['settings']['max_format_attempts']} tries | "
             f"{_rate(s['json']['within_attempts']['pass'], s['json']['within_attempts']['of'])} | |"]
    labels = [("factual_ok", "Factual: no invented amounts"), ("contradiction_free", "No contradictions with the plan"),
              ("flags_ok", "Spending flags valid"), ("missing_ok_when_missing", "Missing info: questions cover every item"),
              ("no_questions_when_complete", "Complete data: no questions asked"),
              ("critical_ok", "Critical facts stated"), ("pass", "All checks pass")]
    for key, label in labels:
        lines.append(f"| {label} | {_rate(s['model'][key]['pass'], s['model'][key]['of'])} | "
                     f"{_rate(s['system'][key]['pass'], s['system'][key]['of'])} |")
    lines += ["", f"- Invented amounts (model): {s['model']['invented_amounts']} in "
              f"{len(s['model']['cases_with_invented_amounts'])} cases; unsupported percentages: "
              f"{s['model']['unsupported_percents']}; unsupported month counts: {s['model']['unsupported_months']}",
              f"- Retries: {s['json']['retries']}; replies cut at the token limit: {s['json']['length_cut'] or 'none'}",
              f"- Emergency fund under 1 month but no warning (model, secondary): "
              f"{len(s['model']['emergency_fund_warning_missing'])} cases"]
    for side in ("model", "system"):
        lines += ["", f"### Contradictions and left-out critical facts ({side})"]
        lines += [f"- {rule}: {len(ids)} ({', '.join(ids)})" for rule, ids in s[side]["contradictions_by_rule"].items()] \
            or ["- no contradictions"]
        lines += [f"- critical fact left out, {k}: {len(v)} ({', '.join(v)})" for k, v in s[side]["critical_left_out"].items()]
    if s["regression_checks"]:
        lines += ["", "## Named regression checks", "| Case | Model | System |", "|---|---|---|"]
        for row in s["regression_checks"]:
            cells = ["pass" if row[side]["pass"] else
                     "FAIL " + (", ".join(row[side]["rules_fired"] + row[side]["left_out"]) or "no valid reply")
                     for side in ("model", "system")]
            lines.append(f"| {row['id']} | {cells[0]} | {cells[1]} |")
    if s.get("timing"):
        t = s["timing"]
        lines += ["", "## Response time",
                  f"- {t['cases']} cases, {t['calls']} model calls: mean {t['mean_s']} s, median {t['median_s']} s, "
                  f"90th percentile {t['p90_s']} s, max {t['max_s']} s, total {t['total_s']} s",
                  f"- Output {t['output_tokens_mean']} tokens per call on average at {t['tokens_per_s_mean']} tokens/s; "
                  f"prompt {t['prompt_tokens_mean']} tokens",
                  f"- Warm-up request (excluded from totals): {m.get('warmup', {}).get('seconds')} s"]
    lines += ["", "## Failed cases (model)", ""]
    for e in case_files.load(run_dir / "cases.jsonl"):
        sc = e.get("scores")
        if e["status"] != "ok" or not sc or not sc["model"]["pass"]:
            lines.append(f"- {e['id']}: " + "; ".join(failure_reasons(e, "model")))
    lines += ["", "## Failed cases (system)", ""]
    for e in case_files.load(run_dir / "cases.jsonl"):
        sc = e.get("scores")
        if e["status"] != "ok" or not sc or not sc["system"]["pass"]:
            lines.append(f"- {e['id']}: " + "; ".join(failure_reasons(e, "system")))
    path = run_dir / "report.md"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


# ------------------------------------------------------------------ running
def run_set(set_name: str, label: str, ai: BudgetAI, recorder: RecordingClient, out_dir: Path,
            limit=None, ids=None, resume_dir: Path = None, warmup=None) -> Path:
    records = load_set(set_name)
    if ids:
        records = [r for r in records if r["id"] in ids]
    if limit:
        records = records[:limit]
    run_dir = resume_dir or out_dir / set_name
    run_dir.mkdir(parents=True, exist_ok=True)
    cases_path = run_dir / "cases.jsonl"
    done = {e["id"] for e in case_files.load(cases_path)} if cases_path.exists() else set()
    meta_path = run_dir / "run.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else {}
    model = {"tag": ai.settings.model_name, "provider": ai.settings.provider}
    if meta:
        prior_model = meta.get("model", {})
        if (prior_model.get("tag"), prior_model.get("provider")) != (model.get("tag"), model.get("provider")):
            raise SystemExit("This set used a different model. Start a new run or rescore the saved results.")
        meta.setdefault("resumed", []).append(datetime.now().isoformat(timespec="seconds"))
    meta.update({
        "set": set_name, "label": label, "set_file": str(SETS[set_name].relative_to(ROOT)).replace("\\", "/"),
        "set_file_sha256": sha256(SETS[set_name]), "cases_planned": len(records),
        "started_at": meta.get("started_at") or datetime.now().isoformat(timespec="seconds"),
        "scorer_version": SCORER_VERSION, "settings": run_settings(ai.settings),
        "model": model, "git": git_info(), "environment": environment(),
        "fingerprints": {f: sha256(ROOT / f) for f in FINGERPRINT_FILES},
        "system_prompt_sha256": hashlib.sha256(ai.instructions.encode()).hexdigest(),
        "command": " ".join(sys.argv), "interrupted": False, "interrupt_reason": None,
    })
    if warmup:
        meta["warmup"] = warmup
    meta_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")

    todo = [r for r in records if r["id"] not in done]
    print(f"\n== {set_name}: {len(records)} cases ({len(done)} already done) -> {run_dir}", flush=True)
    runtime_errors = 0
    started = time.perf_counter()
    try:
        with open(cases_path, "a", encoding="utf-8", newline="\n") as out:
            for i, rec in enumerate(todo, 1):
                entry = evaluate_case(ai, recorder, rec, set_name)
                out.write(json.dumps(entry, ensure_ascii=False) + "\n")
                out.flush()
                verdict = entry["status"] if entry["status"] != "ok" else \
                    ("pass" if entry["scores"]["model"]["pass"] else "fail")
                line = (f"{set_name} {len(done) + i}/{len(records)} {rec['id']}: {verdict}, {entry['seconds']:.1f} s, "
                        f"{entry['attempts']} call(s); elapsed {time.perf_counter() - started:.0f} s")
                print(line, flush=True)
                (run_dir / "progress.txt").write_text(line + "\n", encoding="utf-8")
                runtime_errors = runtime_errors + 1 if entry["status"] == "runtime_error" else 0
                if runtime_errors >= STOP_AFTER_RUNTIME_ERRORS:
                    meta.update(interrupted=True,
                                interrupt_reason=f"{runtime_errors} runtime errors in a row: {entry['error']}")
                    break
    except KeyboardInterrupt:
        meta.update(interrupted=True, interrupt_reason="stopped by the user (Ctrl+C)")
    except ModelRateLimitError as exc:
        meta.update(interrupted=True, interrupt_reason=str(exc))
        finish(run_dir, meta)
        print(f'Resume this set after the quota resets with --resume "{run_dir}"', flush=True)
        raise
    finish(run_dir, meta)
    return run_dir


def finish(run_dir: Path, meta: dict) -> dict:
    entries = case_files.load(run_dir / "cases.jsonl") if (run_dir / "cases.jsonl").exists() else []
    meta["finished_at"] = datetime.now().isoformat(timespec="seconds")
    summary = summarize(entries, meta)
    (run_dir / "run.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    (run_dir / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    report = write_report(run_dir, meta, summary)
    print(f"Saved {report}", flush=True)
    return summary


def warm_up(ai: BudgetAI) -> dict:
    start = time.perf_counter()
    try:
        ai.generate_budget(WARMUP_PROFILE)
        error = None
    except InvalidModelResponseError as exc:
        error = str(exc)
    return {"seconds": round(time.perf_counter() - start, 2), "error": error}


def planner_only(set_names: list) -> int:
    failures = 0
    for name in set_names:
        if not SETS[name].exists():
            print(f"{name}: no case file yet ({SETS[name].name}); skipped")
            continue
        records = load_set(name)
        failed = [(r["id"], c) for r in records for c in [planner_check(r)] if not c["ok"]]
        failures += len(failed)
        print(f"{name}: planner reproduces the expected plan and passes every check in "
              f"{len(records) - len(failed)}/{len(records)} cases")
        for rid, c in failed:
            print(f"  {rid}: reproduced={c['reproduced']} {c['independent_problems'][:2]} {c['hand_problems'][:2]}")
    return failures


# ------------------------------------------------------------------ rescoring and comparing
def rescore(run_dir: Path) -> dict:
    """Score saved replies again with the current scorer (no model calls)."""
    path = run_dir / "cases.jsonl"
    entries = case_files.load(path)
    for e in entries:
        e["scores"] = score_entry(e) if e["status"] != "runtime_error" else None
    case_files.write(path, entries)
    meta = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
    meta["rescored_at"] = datetime.now().isoformat(timespec="seconds")
    meta["scorer_version"] = SCORER_VERSION
    return finish(run_dir, meta)


COMPARE_ROWS = [("json", "first_try", "Valid JSON on the first try"),
                ("json", "within_attempts", "Valid JSON within the allowed tries"),
                ("model", "factual_ok", "Model: no invented amounts"),
                ("model", "contradiction_free", "Model: no contradictions"),
                ("model", "flags_ok", "Model: spending flags valid"),
                ("model", "missing_ok_when_missing", "Model: questions cover missing items"),
                ("model", "no_questions_when_complete", "Model: no questions when complete"),
                ("model", "critical_ok", "Model: critical facts stated"),
                ("model", "pass", "Model: all checks"),
                ("system", "factual_ok", "System: no invented amounts"),
                ("system", "contradiction_free", "System: no contradictions"),
                ("system", "flags_ok", "System: spending flags valid"),
                ("system", "missing_ok_when_missing", "System: questions cover missing items"),
                ("system", "no_questions_when_complete", "System: no questions when complete"),
                ("system", "critical_ok", "System: critical facts stated"),
                ("system", "pass", "System: all checks")]


def compare(before: Path, after: Path) -> Path:
    a, b = rescore(before), rescore(after)
    lines = [f"# Comparison: {before.parent.name}/{before.name} -> {after.parent.name}/{after.name}",
             f"(both scored with scorer {SCORER_VERSION})", "",
             f"Cases ran: {a['cases_ran']} -> {b['cases_ran']}", "",
             "| Metric | Before | After | Newly failing | Newly passing |", "|---|---|---|---|---|"]
    for group, key, label in COMPARE_ROWS:
        x, y = a[group][key], b[group][key]
        worse = sorted(set(y["failed_ids"]) - set(x["failed_ids"]))
        better = sorted(set(x["failed_ids"]) - set(y["failed_ids"]))
        lines.append(f"| {label} | {_rate(x['pass'], x['of'])} | {_rate(y['pass'], y['of'])} | "
                     f"{', '.join(worse) or '-'} | {', '.join(better) or '-'} |")
    for side in ("model", "system"):
        lines.append(f"\nInvented amounts ({side}): {a[side]['invented_amounts']} -> {b[side]['invented_amounts']}")
        for r in sorted(set(a[side]["contradictions_by_rule"]) | set(b[side]["contradictions_by_rule"])):
            lines.append(f"- {r} ({side}): {len(a[side]['contradictions_by_rule'].get(r, []))} -> "
                         f"{len(b[side]['contradictions_by_rule'].get(r, []))}")
    if a.get("timing") and b.get("timing"):
        lines.append(f"\nMean seconds per case: {a['timing']['mean_s']} -> {b['timing']['mean_s']}; "
                     f"median {a['timing']['median_s']} -> {b['timing']['median_s']}")
    out = after / f"compare_vs_{before.parent.name}.md"
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    return out


# ------------------------------------------------------------------ command line
def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--set", nargs="+", choices=sorted(SETS), default=["heldout"])
    parser.add_argument("--label", default="run", help="a short name for this run, e.g. baseline")
    parser.add_argument("--limit", type=int, help="only the first N cases of each set")
    parser.add_argument("--ids", help="comma-separated case IDs")
    parser.add_argument("--planner-only", action="store_true", help="check the planner without the model")
    parser.add_argument("--resume", type=Path, help="continue an interrupted set folder")
    parser.add_argument("--rescore", type=Path, nargs="+", help="score saved set folders again")
    parser.add_argument("--compare", type=Path, nargs=2, metavar=("BEFORE", "AFTER"))
    args = parser.parse_args(argv)

    if args.planner_only:
        sys.exit(1 if planner_only(args.set) else 0)
    if args.rescore:
        for d in args.rescore:
            rescore(d)
        return
    if args.compare:
        compare(*args.compare)
        return

    settings = config.load_settings()
    ids = set(args.ids.split(",")) if args.ids else None
    try:
        with GroqAIService(settings) as client:
            recorder = RecordingClient(client)
            ai = BudgetAI(settings=settings, client=recorder)
            print(f"Testing {settings.model_name} on Groq ...", flush=True)
            warmup = warm_up(ai)
            print(f"Warm-up: {warmup}", flush=True)
            if args.resume:
                meta = json.loads((args.resume / "run.json").read_text(encoding="utf-8"))
                run_set(meta["set"], meta["label"], ai, recorder, args.resume.parent,
                        args.limit, ids, args.resume, warmup)
                return
            stamp = datetime.now().strftime("%Y%m%d-%H%M")
            out_dir = RESULTS_DIR / f"{args.label}-{stamp}"
            for name in args.set:
                run_set(name, args.label, ai, recorder, out_dir, args.limit, ids, warmup=warmup)
            print(f"\nAll results are in {out_dir}")
    except BudgetAIError as exc:
        print(f"Evaluation stopped: {exc}", file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()
