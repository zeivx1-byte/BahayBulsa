# BudgetAI dataset

412 realistic Philippine household scenarios, each with the **expected budget**
calculated by BudgetAI's budgeting rules (rules version 1.1) and checked by an
independent validator.

| File | Scenarios | Used for |
|---|---|---|
| `budgeting_examples.jsonl` | 300 | Examples, tuning and (if ever needed) fine-tuning |
| `test_scenarios.jsonl` | 112 | Held-out evaluation in Phase 6 (102 generated + 10 hand-checked "golden") |

## Commands (from the BudgetAI_Model folder, virtual environment active)

```powershell
python dataset/generate_dataset.py      # rebuild both files (same seed = same files)
python dataset/validate_dataset.py      # check every scenario
python dataset/show_scenario.py BA-0001 # look at one scenario
```

## Scenario types (train / test)

| Type | Count | What it covers |
|---|---|---|
| `low_income` | 30 / 10 | Families making ends meet (no deficit) |
| `middle_income` | 30 / 10 | Typical lower-middle and middle-class budgets |
| `high_income` | 30 / 10 | Upper-middle and high incomes |
| `debt_heavy` | 30 / 10 | At least one high-interest debt (credit card, loan app, personal loan, motorcycle) |
| `irregular_income` | 30 / 10 | Freelancers, store owners, drivers, remittances; half in deficit on a low month |
| `high_fixed_expenses` | 30 / 10 | High rent and tuition: fixed costs at least 45% of income |
| `aggressive_savings` | 30 / 10 | Savings goals that cannot be reached on time |
| `deficit` | 30 / 10 | Income below required spending |
| `spending_habits` | 30 / 10 | Half frugal (2-6% on wants), half big spenders (15-28%) |
| `incomplete` | 15 / 7 | Missing expenses, household size, interest rates, low month or goal deadline |
| `edge_case` | 15 / 5 | Unusual but valid: full emergency fund, no wants, income = required, user overrides, NCR minimum wage, zero amounts |
| `golden` | 0 / 10 | Amounts worked out by hand (see `GOLDEN` in `generate_dataset.py`) |

## One line = one scenario

```json
{
  "id": "BA-0001", "split": "train", "scenario_type": "low_income",
  "title": "Gonzales family of 2 in Cagayan de Oro: low income, making ends meet",
  "rules_version": "1.1",
  "profile":  { ...the household, in the FinancialProfile format... },
  "expected": { "facts": {...}, "budget": [...], "total_allocated": ..., "unallocated": ...,
                "unfunded": ..., "rule_notes": [...], "action_steps": [...], "missing_data": [...] },
  "hand_checked": { "Emergency fund": 2750, ... }   // golden scenarios only
}
```

## How realism was ensured

Incomes and costs come from published Philippine figures, adjusted to 2026:

- **Income bands** (monthly family income): low 14,000-28,000; lower-middle 28,000-57,000;
  middle 57,000-100,000; upper-middle 100,000-170,000; high 170,000-300,000. These are the
  PIDS income-class brackets for a family of five (2018 prices: low income 10,957-21,914,
  lower middle 21,914-43,828, middle 43,828-76,669, upper middle 76,669-131,484) raised by
  about 30% for inflation.
- **Reference points**: PSA 2023 FIES average annual family income of PHP 353,230; 2023 poverty
  threshold of PHP 13,873 a month for a family of five; NCR non-agriculture minimum wage of
  PHP 755 a day (Wage Order NCR-28, September 2026), about PHP 19,630 a month.
- **Spending patterns**: food per person, rent share, electricity, transport, school costs and
  family support scale with income; lower-income households spend a larger share on food.
  Debts use typical Philippine products and rates (credit cards 36% a year, loan apps 120-240%,
  SSS and Pag-IBIG loans about 10%, Pag-IBIG housing 6.25%).

Sources: PSA 2023 Family Income and Expenditure Survey (psa.gov.ph); PIDS income class brackets
(pids.gov.ph); DOLE / RTWPB-NCR Wage Order NCR-28.

## How correctness is ensured

`validate_dataset.py` checks every scenario four ways:
1. The profile is valid and was made with the current rules version.
2. The current planner reproduces the expected budget exactly.
3. **Independent arithmetic**, written separately from the budgeting code, re-checks totals,
   "never above income", "every peso assigned", required spending first, the deficit order,
   the future share, the caps, and that action steps only use real amounts. It also checks
   the Phase 5 rules: every amount (profiles included) is a whole number of centavos and the
   lines shown add up to the total shown; a debt with a minimum above its balance is not
   budgeted but explained and asked about; a partial plan says so in its notes, lines and
   steps; irregular income without a low month gets a note that the stated amount was used.
4. The 10 golden scenarios match amounts worked out by hand.

The validator found one real bug while the dataset was built: the rules used the rounded
emergency-fund figure (1.00 months) instead of the exact one (0.999 months). It was fixed.

If a budgeting rule changes, raise `RULES_VERSION` in `budgeting_engine/allocation.py`,
regenerate, and validate again.

**Rules 1.1 (Phase 5).** Regenerating changed no budget amount. All 412 records got the new
rules label, and 5 records (BA-0363, BA-0367, BA-0371, BA-0375 in train; BA-0379 in test)
gained a note that their irregular income had no low month, so the stated monthly amount was
used. IDs, profiles and the train/test split are unchanged. File fingerprints (SHA-256):
- `budgeting_examples.jsonl`: `0d735328f6f7dfdf1f452c3ca5bc700c855b6721482921830e24c24b0161e500`
- `test_scenarios.jsonl`: `f5bced2524d72c47a8c447487758af4caf54034305590921a82096ed5395ee0c`
