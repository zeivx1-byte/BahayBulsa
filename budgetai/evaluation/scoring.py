"""Deterministic scoring of the AI's explanations (Phase 6).

The model only explains; Python's plan is the truth. Every check below
compares the model's text with the exact prompt it saw and the final plan:

- valid JSON         the reply parsed into AIBudgetAdvice on the first try
- factual accuracy   every money amount (and percentage) the model writes was
                     shown in its prompt (rounded to the peso is fine); anything
                     else is an invented amount
- contradictions     sentences that disagree with the plan: a deficit described
                     as fine, money "set aside" that the plan does not allocate
                     (Reyes), extra saving with no source (Santos), a weak
                     emergency fund called healthy, a held debt to be paid, a
                     partial plan called "no budget", a low month that was never
                     given, advice to stop paying debts
- spending flags     suggested cuts must name a listed essential/obligation
                     expense and suggest a lower amount above 0
- missing info       questions cover every missing item, and none are asked
                     when nothing is missing
- critical facts     facts the explanation must state: the shortfall in a
                     deficit, a debt on hold, a partial plan, irregular income
                     planned on its stated amount

Scores are pure functions of saved data, so any run can be rescored with the
same rules (SCORER_VERSION) for a fair before/after comparison.
"""
import re

from budgeting_engine.validation import _covers
from model.schemas import FinancialProfile

SCORER_VERSION = "1.1"
PESO_TOLERANCE = 0.5          # "3120" for 3120.30 is fine; "3000" is not
STANDARD_MONTHS = {1.0, 3.0, 6.0}

# ------------------------------------------------------------------ numbers
_NUMBER = re.compile(
    r"(?<![\w.])(?P<cur>(?:PHP|Php|₱)\s?|P(?=\d))?"
    r"(?P<num>\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)"
)
_PCT_AFTER = re.compile(r"\s?(?:%|percent\b)", re.I)
_MONTH_AFTER = re.compile(r"(?:\s|-)?(?:months?|mos?)\b", re.I)
_COUNT_AFTER = re.compile(r"\s?(?:people|persons?|members?|children|kids|years?|days?|weeks?|times|"
                          r"debts?|loans?|items?|lines?|steps?|sentences?|x)\b", re.I)
_CUR_AFTER = re.compile(r"\s?(?:PHP|pesos?)\b", re.I)
_APPROX_BEFORE = re.compile(r"(?:about|around|roughly|nearly|almost|approximately|over|under|"
                            r"more than|less than|up to)\s*(?:PHP|₱)?\s*$", re.I)


def find_numbers(text: str) -> list:
    """Every number in a text with its kind: money, percent, months, count or small."""
    found = []
    for m in _NUMBER.finditer(text or ""):
        value = float(m.group("num").replace(",", ""))
        after = text[m.end():m.end() + 12]
        if _PCT_AFTER.match(after):
            kind = "percent"
        elif _MONTH_AFTER.match(after):
            kind = "months"
        elif _COUNT_AFTER.match(after) and not m.group("cur"):
            kind = "count"
        elif m.group("cur") or _CUR_AFTER.match(after) or value >= 50:
            kind = "money"
        else:
            kind = "small"            # 1, 2, 0.25 ... without a unit: not checked
        approx = bool(_APPROX_BEFORE.search(text[max(0, m.start() - 22):m.start()]))
        found.append({"value": value, "kind": kind, "text": m.group(0), "approx": approx,
                      "start": m.start(), "end": m.end()})
    return found


def _matches(value: float, known, approx: bool = False) -> bool:
    for k in known:
        if abs(value - k) <= PESO_TOLERANCE:
            return True
        if approx and k and abs(value - k) / abs(k) <= 0.01:
            return True
    return False


# ------------------------------------------------------------------ text helpers
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")
_NEGATION = re.compile(r"\b(?:not|never|no|cannot|can't|isn't|aren't|don't|doesn't|won't|without)\b[^.]{0,25}$", re.I)


def advice_sentences(advice: dict) -> list:
    """(field, sentence) pairs for every text the household will read."""
    out = []
    for field in ("summary", "savings_advice"):
        out += [(field, s) for s in _SENTENCE_END.split(advice.get(field) or "") if s.strip()]
    for field in ("warnings", "missing_information"):
        for item in advice.get(field) or []:
            out += [(field, s) for s in _SENTENCE_END.split(item) if s.strip()]
    for flag in advice.get("spending_flags") or []:
        out += [("spending_flags", s) for s in _SENTENCE_END.split(flag.get("reason") or "") if s.strip()]
    return out


def _hit(pattern, sentence: str):
    """First non-negated match of pattern in sentence, or None."""
    for m in re.finditer(pattern, sentence, re.I):
        if not _NEGATION.search(sentence[:m.start()]):
            return m
    return None


# ------------------------------------------------------------------ plan context
def plan_context(profile: dict, plan: dict, prompt: str) -> dict:
    """Everything the checks need, derived only from the plan and the prompt."""
    lines = plan["budget"]
    facts = plan["facts"]
    prof = FinancialProfile.model_validate(profile)
    in_prompt = find_numbers(prompt)
    known_money = {n["value"] for n in in_prompt}
    known_pct = {n["value"] for n in in_prompt if n["kind"] == "percent"}
    known_months = {n["value"] for n in in_prompt if n["kind"] == "months"} | STANDARD_MONTHS
    emergency_months = facts.get("emergency_fund_months")
    if emergency_months is not None:
        known_months.add(round(emergency_months, 2))

    groups = {}
    for line in lines:
        groups.setdefault(line["category"], 0.0)
        groups[line["category"]] += line["amount"]
    by_priority = {}
    for line in lines:
        by_priority[line["priority"]] = by_priority.get(line["priority"], 0.0) + line["amount"]
    allocated = {l["amount"] for l in lines if l["amount"] > 0}
    allocated |= {round(v, 2) for v in groups.values() if v > 0}
    allocated |= {round(v, 2) for v in by_priority.values() if v > 0}
    allocated.add(plan["total_allocated"])
    savings_lines = [l for l in lines if l["category"] in ("emergency_fund", "savings_goal", "extra_savings")]
    if savings_lines:
        allocated.add(round(sum(l["amount"] for l in savings_lines), 2))

    held = [d for d in prof.debts if d.minimum_payment > d.balance]
    stated_only = [i for i in prof.incomes if not i.regular and i.minimum_monthly_amount is None]
    has_low_month = any(not i.regular and i.minimum_monthly_amount is not None for i in prof.incomes)
    return {
        "facts": facts, "lines": lines, "missing": plan["missing_data"], "profile": prof,
        "is_partial": facts["situation"] == "incomplete" and bool(lines),
        "weak_emergency_fund": emergency_months is not None and emergency_months < 1,
        "known_money": known_money, "known_pct": known_pct, "known_months": known_months,
        "allocated": allocated, "unallocated": plan["unallocated"],
        "line_names": [l["name"] for l in lines] + [e.name for e in prof.expenses],
        "held": held, "stated_only": stated_only, "has_low_month": has_low_month,
    }


# ------------------------------------------------------------------ the checks
def check_amounts(advice: dict, ctx: dict) -> dict:
    """Money amounts and percentages that are not in the prompt."""
    invented, pct, months = [], [], []
    flag_amounts = {f.get("suggested_amount", 0) for f in advice.get("spending_flags") or []}
    flag_amounts |= {f.get("current_amount", 0) for f in advice.get("spending_flags") or []}
    for field, sentence in advice_sentences(advice):
        for n in find_numbers(sentence):
            if n["kind"] == "money":
                ok = _matches(n["value"], ctx["known_money"], n["approx"])
                if field == "spending_flags":
                    ok = ok or _matches(n["value"], flag_amounts)
                if not ok:
                    invented.append({"field": field, "value": n["value"], "text": n["text"], "sentence": sentence})
            elif n["kind"] == "percent" and not _matches(n["value"], ctx["known_pct"]):
                pct.append({"field": field, "value": n["value"], "sentence": sentence})
            elif n["kind"] == "months" and not any(abs(n["value"] - k) < 0.01 for k in ctx["known_months"]):
                months.append({"field": field, "value": n["value"], "sentence": sentence})
    return {"invented_amounts": invented, "unsupported_percents": pct, "unsupported_months": months}


_SET_ASIDE = r"\b(?:set aside|put aside|allocated|budgeted|reserved|earmarked|kept for|have saved)\b"
_SAVE_VERB = r"\b(?:save|saving|savings|put|set aside|add|adding|contribute|deposit|transfer|move|increase)\b"
_EXTRA = r"\b(?:extra|additional|another|more|added)\b"
_SOURCE = r"\b(?:by|from)\s+(?:cutting|reducing|lowering|trimming|moving|taking|using)\b|\bfrom (?:your |the )"
_HEALTHY = r"\b(?:healthy|solid|strong|good|sufficient|adequate|comfortable)\b"
_BUILD = re.compile(r"\b(?:build|building|grow|growing|start|toward|towards|aim|reach|create|having)\b[^.]{0,30}$", re.I)
_DEFICIT_DENIED = (r"\b(?:stable|surplus|room to save|left over|extra money|money to spare|"
                   r"all (?:of )?(?:your )?(?:essential |basic )?(?:needs|expenses|bills) (?:are )?(?:fully )?(?:met|covered|paid)|"
                   r"everything is (?:funded|covered|paid)|no shortfall)\b")
_FALSE_DEFICIT = (r"\b(?:deficit|shortfall|short by|not enough (?:money|income) to cover|"
                  r"can(?:no|')t cover (?:your )?(?:essential|basic|required))")
_NO_BUDGET = r"\b(?:no budget (?:can|could) be made|cannot (?:make|create|build) a budget|no budget (?:yet|was made|is made))"
_USED_LOW_MONTH = r"\b(?:uses?|used|based on|planned (?:on|for|with)|assum\w+)\b[^.]{0,40}\blow(?:est)?[- ](?:income )?month"
_STOP_PAYING = r"\b(?:stop|skip|pause|halt|suspend)\b[^.]{0,25}\b(?:paying|payments?)\b"
_ALL_TO_SAVINGS = r"\b(?:all|whole|entire)\b (?:of )?(?:your )?income\b[^.]{0,25}\b(?:sav|invest)"


def check_contradictions(advice: dict, ctx: dict) -> list:
    facts = ctx["facts"]
    found = []

    def add(rule, field, sentence, detail=""):
        found.append({"rule": rule, "field": field, "sentence": sentence, "detail": detail})

    for field, s in advice_sentences(advice):
        if field == "missing_information":
            continue
        money = [n for n in find_numbers(s) if n["kind"] == "money"]
        if facts["is_deficit"] and _hit(_DEFICIT_DENIED, s):
            add("deficit_denied", field, s)
        if not facts["is_deficit"] and facts["situation"] != "incomplete" and _hit(_FALSE_DEFICIT, s):
            add("false_deficit", field, s)
        if ctx["weak_emergency_fund"] and re.search(r"emergency fund", s, re.I):
            m = _hit(_HEALTHY, s)
            if m and not _BUILD.search(s[:m.start()]):
                add("weak_emergency_fund_praised", field, s)
        if _hit(_SET_ASIDE, s):
            for n in money:
                if not _matches(n["value"], ctx["allocated"]) and not re.search(r"unallocated|not allocated", s, re.I):
                    add("set_aside_not_in_plan", field, s, f"{n['value']:.2f} is not allocated in the plan")
        if _hit(_SAVE_VERB, s) and _hit(_EXTRA, s) and not re.search(_SOURCE, s, re.I):
            for n in money:
                if not _matches(n["value"], ctx["allocated"] | {ctx["unallocated"]}):
                    add("unplanned_extra_saving", field, s, f"{n['value']:.2f} more saving with no source in the plan")
        for d in ctx["held"]:
            if d.name.lower() in s.lower() and re.search(r"\bpay", s, re.I) \
                    and not re.search(r"confirm|not (?:in|included|budgeted)|on hold|until|left out|held", s, re.I):
                add("held_debt_paid", field, s, f"'{d.name}' is on hold")
        if ctx["is_partial"] and _hit(_NO_BUDGET, s):
            add("partial_called_no_budget", field, s)
        if ctx["stated_only"] and not ctx["has_low_month"] and _hit(_USED_LOW_MONTH, s):
            add("false_low_month", field, s)
        if _hit(_STOP_PAYING, s) and not any(d.name.lower() in s.lower() for d in ctx["held"]):
            add("stop_paying_debt", field, s)
        if _hit(_ALL_TO_SAVINGS, s):
            add("whole_income_to_savings", field, s)
    return found


def check_flags(advice: dict, ctx: dict) -> list:
    from budgeting_engine.calculations import expense_kind
    problems = []
    for flag in advice.get("spending_flags") or []:
        name = (flag.get("expense_name") or "").lower().strip()
        match = None
        for e in ctx["profile"].expenses:
            if e.name.lower() == name or e.name.lower() in name or name in e.name.lower():
                match = e
                break
        if match is None:
            problems.append({"flag": flag.get("expense_name"), "problem": "not a listed expense"})
        elif expense_kind(match) == "non_essential":
            planned = [l["amount"] for l in ctx["lines"] if l["name"] == match.name]
            if not (planned and abs((flag.get("suggested_amount") or 0) - planned[0]) <= 0.005):
                problems.append({"flag": flag.get("expense_name"),
                                 "problem": "non-essential cut that differs from the plan (Python sets these)"})
        elif not 0 < (flag.get("suggested_amount") or 0) < match.monthly_amount:
            problems.append({"flag": flag.get("expense_name"), "problem": "suggested amount is not lower and above 0"})
    return problems


def check_missing(advice: dict, ctx: dict) -> dict:
    questions = advice.get("missing_information") or []
    missing = ctx["missing"]
    if not missing:
        return {"applicable": False, "ok": not questions, "uncovered": [], "extra": questions}
    uncovered = [m for m in missing if not any(_covers(m, q) for q in questions)]
    extra = [q for q in questions if not any(_covers(m, q) for m in missing)]
    return {"applicable": True, "ok": not uncovered and not extra, "uncovered": uncovered, "extra": extra}


def critical_required(ctx: dict) -> list:
    """The critical facts this plan requires the explanation to state."""
    required = []
    if ctx["facts"]["is_deficit"]:
        required.append("deficit_shortfall")
    required += [f"held_debt:{d.name}" for d in ctx["held"]]
    if ctx["is_partial"]:
        required.append("partial_plan")
    if ctx["stated_only"] and not ctx["has_low_month"]:
        required.append("irregular_stated_amount")
    return required


def check_critical(advice: dict, ctx: dict) -> list:
    """Critical facts the explanation must state. Returns the ones it leaves out."""
    facts = ctx["facts"]
    text = " ".join(s for _, s in advice_sentences(advice))
    head = " ".join([advice.get("summary") or ""] + list(advice.get("warnings") or []))
    left_out = []
    for item in critical_required(ctx):
        if item == "deficit_shortfall":
            shown = [n["value"] for n in find_numbers(head) if n["kind"] == "money"]
            ok = _matches(facts["shortfall"], shown)
        elif item.startswith("held_debt:"):
            name = item.split(":", 1)[1].lower()
            ok = any(name in s.lower() and re.search(r"confirm|not (?:in|included in) the budget|not budgeted|"
                                                     r"on hold|left out", s, re.I)
                     for _, s in advice_sentences(advice))
        elif item == "partial_plan":
            ok = "partial plan" in (advice.get("summary") or "").lower()
        else:   # irregular_stated_amount
            ok = bool(re.search(r"\bstated\b|\blow(?:er|est)? month", text, re.I))
        if not ok:
            left_out.append(item)
    return left_out


def check_secondary(advice: dict, ctx: dict) -> list:
    """Useful but not critical: an emergency fund under 1 month is mentioned in warnings."""
    facts = ctx["facts"]
    out = []
    if (facts["situation"] in ("stable", "tight") and ctx["weak_emergency_fund"]
            and not any("emergency" in w.lower() for w in advice.get("warnings") or [])):
        out.append("emergency_fund_warning")
    return out


def score_advice(advice, ctx: dict) -> dict:
    """All checks for one advice object (a dict), or a failure record if there is none."""
    if advice is None:
        return {"has_advice": False, "pass": False}
    amounts = check_amounts(advice, ctx)
    contradictions = check_contradictions(advice, ctx)
    flags = check_flags(advice, ctx)
    missing = check_missing(advice, ctx)
    critical = check_critical(advice, ctx)
    factual_ok = not amounts["invented_amounts"] and not amounts["unsupported_percents"] \
        and not amounts["unsupported_months"]
    return {
        "has_advice": True, **amounts, "factual_ok": factual_ok,
        "contradictions": contradictions, "contradiction_free": not contradictions,
        "flag_problems": flags, "flags_ok": not flags,
        "missing": missing, "missing_ok": missing["ok"],
        "critical_required": critical_required(ctx), "critical_missing": critical, "critical_ok": not critical,
        "secondary_missing": check_secondary(advice, ctx),
        "pass": factual_ok and not contradictions and not flags and missing["ok"] and not critical,
    }


def score_entry(entry: dict) -> dict:
    """Score one saved evaluation entry (raw model advice and final system advice)."""
    ctx = plan_context(entry["profile"], entry["plan"], entry.get("prompt") or "")
    raw = score_advice(entry.get("raw_advice"), ctx)
    final = score_advice(entry.get("final_advice"), ctx)
    raw["json_first_try"] = entry.get("json_first_try", False)
    raw["pass"] = raw["pass"] and raw["json_first_try"]
    return {"scorer_version": SCORER_VERSION, "model": raw, "system": final}
