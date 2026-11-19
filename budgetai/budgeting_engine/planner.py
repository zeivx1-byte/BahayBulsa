"""The complete rule-based plan for one household, with no AI.

Fast (a few milliseconds) and deterministic: the same profile always gives
the same plan. Used by BudgetAI before asking the AI to explain, by the
dataset generator to create expected budgets, and later by the API for an
instant budget.
"""
from typing import Union

from budgeting_engine.allocation import RULES_VERSION, build_budget
from budgeting_engine.calculations import money, summarize
from budgeting_engine.insights import action_steps
from budgeting_engine.validation import find_missing_data
from model.schemas import BudgetPlan, FinancialProfile


def make_plan(profile: Union[FinancialProfile, dict], currency: str = "PHP") -> BudgetPlan:
    """Facts, budget, notes, action steps and missing data for one household."""
    if isinstance(profile, dict):
        profile = FinancialProfile.model_validate(profile)
    facts = summarize(profile)
    lines, notes = build_budget(profile, facts)
    total = money(sum(line.amount for line in lines))
    return BudgetPlan(
        rules_version=RULES_VERSION,
        currency=currency,
        facts=facts,
        budget=lines,
        total_allocated=total,
        unallocated=money(facts.planning_income - total),
        unfunded=money(sum(line.needed_amount - line.amount for line in lines if line.source == "required")),
        rule_notes=notes,
        action_steps=action_steps(profile, facts, lines, currency),
        missing_data=find_missing_data(profile),
    )
