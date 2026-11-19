# BudgetAI Model

A specialized AI budgeting system for households in the Philippines. Its Python
backend uses Groq's `openai/gpt-oss-20b` model for chat and budget explanations.

**Design rule:** the AI understands situations and explains recommendations;
plain Python code does every calculation and enforces every rule (for example,
a budget can never exceed income).

**How a budget is made:**
1. Python calculates the facts (totals, deficit, emergency-fund months).
2. Python builds the budget with fixed, explainable rules (see the top of
   `budgeting_engine/allocation.py`): required spending first; in a deficit,
   essentials first; otherwise the money left is split between savings/debt
   payoff and non-essentials depending on how urgent the situation is.
3. Python writes the action steps for the month from the budget.
   The AI explains the final budget: a summary, personal advice, suggested
   cuts to essentials, warnings and questions about missing information.
4. Python checks the AI's suggestions and writes critical warnings from the plan.

**Money policy:** every amount is rounded to the nearest centavo, halves up
(₱0.006 becomes ₱0.01, ₱0.004 becomes ₱0.00). Inputs are rounded when the
profile is checked and every result is rounded the same way, so the budget
lines shown always add up to the total shown. An income that rounds to ₱0.00
is rejected.

**Inconsistent or incomplete data (rules 1.1):**
- A debt whose minimum payment is more than its balance is not budgeted; the
  plan says why and asks the user to confirm both amounts.
- Debts but no expenses: only the known minimum payments are budgeted, and
  the plan, the facts, the steps and the AI summary all call it a partial plan.
- Irregular income without a reported low month: the plan says that it used
  the stated monthly amount.

## Project status

| Phase | Status |
|---|---|
| 1. Environment setup | Done |
| 2. Project structure | Done |
| 3. Budgeting intelligence (rules, instructions, JSON formats) | Done |
| 4. Dataset (412 scenarios with checked expected budgets) | Done |
| 5. Budgeting engine bug fixes (money policy, inconsistent and incomplete data) | Done |
| 6. Evaluation and improvement | Done |

## Folder structure

```
BudgetAI_Model/
├── model/                    AI layer
│   ├── ai_service.py         Groq SDK transport and error handling
│   ├── schemas.py            data formats: profile in, AI JSON out, checked result
│   ├── budgeting_ai.py       builds the budget with Python, then asks the model to explain it
│   ├── budget_chat.py        resolves questions, follow-ups and expense scenarios
│   ├── spending_chat.py      writes replies from verified spending facts
│   └── instructions.txt      BudgetAI's standing instructions (system prompt)
├── budgeting_engine/         deterministic Python math and rule checks
│   ├── calculations.py       exact facts: totals, deficit, emergency-fund months
│   ├── allocation.py         the budgeting rules; never exceeds income
│   ├── planner.py            the full rule-based plan without the AI (fast)
│   ├── insights.py           plain-language facts for the AI and action steps for the user
│   ├── spending.py           calculates and compares recorded expenses
│   └── validation.py         missing-data detection and AI advice clean-up
├── dataset/                  412 household scenarios with expected budgets
│   ├── generate_dataset.py   builds the scenarios (same seed = same files)
│   ├── validate_dataset.py   checks every expected budget independently
│   ├── show_scenario.py      prints one scenario
│   ├── budgeting_examples.jsonl   300 scenarios
│   └── test_scenarios.jsonl       112 held-out scenarios (10 hand-checked)
├── evaluation/               regression and external-data challenge cases
│   ├── evaluate_model.py     runs the model, saves replies and reports results
│   └── scoring.py            checks raw replies and the final advice separately
├── tests/                    automated tests (run with: pytest)
├── config.py                 reads and checks all settings from .env
├── check_setup.py            checks a structured Groq reply with a fictional household
├── try_budgetai.py           runs BudgetAI on four sample households
├── .env                      private settings for this computer (not in Git)
├── .env.example              template for .env (safe to share)
├── requirements.txt          exact Python library versions
├── pytest.ini                test settings
├── .gitignore                files Git must never save (.env, .venv, caches)
└── .gitattributes            consistent line endings on Windows and Linux
```

## Setup on a new Windows computer

Requires Python 3.10+, Git, internet access, and a Groq Free Plan key for AI
features.

```powershell
git clone <your-repository-url> BudgetAI_Model
cd BudgetAI_Model
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
```

Sign in to Groq Console, select a Free Plan organization, and create an API key.
Paste the value after `GROQ_API_KEY=` in your private `.env`. You can also set
`$env:GROQ_API_KEY` in the PowerShell session that launches the Python process.
Set `BUDGETAI_MODEL` to a Groq model ID to override the default.
[Groq API keys](https://console.groq.com/keys) · [Groq quickstart](https://console.groq.com/docs/quickstart)

For the BahayBulsa desktop app, use its bundled backend settings and run
`python main.py` from the BahayBulsa application folder. This opens PyWebView
and uses the Python Groq integration directly. See the app's README for setup.

## Daily use

```powershell
cd $HOME\Documents\BudgetAI_Model
.\.venv\Scripts\Activate.ps1
python check_setup.py   # one live Groq request with fictional budget data
python try_budgetai.py  # BudgetAI on four sample households (uses the model)
python dataset/validate_dataset.py   # check the dataset
pytest                  # automated tests with test doubles for model calls
```

In Windows Command Prompt, run the setup check and a sample budget with the
project's Python executable.

```bat
cd /d "%USERPROFILE%\Documents\BudgetAI_Model"
.\.venv\Scripts\python.exe check_setup.py
.\.venv\Scripts\python.exe try_budgetai.py 1
```

## Groq Free Plan limits

| Limit for `openai/gpt-oss-20b` | Published value |
|---|---:|
| Requests per minute | 30 |
| Requests per day | 1,000 |
| Tokens per minute | 8,000 |
| Tokens per day | 200,000 |
| Model context window | 131,072 tokens |
| Model completion limit | 65,536 tokens |

The quotas are per organization. Check your Groq Console for your organization's
limits. BudgetAI uses smaller prompts and completion budgets. If Groq returns a
rate limit, follow the retry timing from Groq when available. The Free Plan
quotas can interrupt a large evaluation run.
[Groq rate limits](https://console.groq.com/docs/rate-limits) · [Organization limits](https://console.groq.com/settings/limits) · [GPT-OSS 20B](https://console.groq.com/docs/model/openai/gpt-oss-20b)

## Phase 6 evaluation

The evaluation checks five named regressions, the 112 original held-out cases,
and 20 synthetic challenge cases across 18 Philippine regions. Each challenge
case records its public aggregate source, household assumptions, and
hand-checked budget amounts. The inputs use regional family income and spending
averages and national savings and debt patterns for context.
[PSA 2023 FIES preliminary tables](https://psa.gov.ph/content/average-annual-family-income-2023-estimated-php-35323-thousand) ·
[BSP 2021 Consumer Finance Survey](https://www.bsp.gov.ph/Media_And_Research/Consumer%20Finance%20Survey/CFS_2021.pdf)

```powershell
python -m evaluation.cases --check
python -m evaluation.evaluate_model --planner-only --set regression heldout challenge
python -m evaluation.evaluate_model --set regression --limit 2 --label groq-20b
```

The evaluator saves case-level replies, model settings, Git and input fingerprints,
scores, and a report for each set under `results/evaluation/`. The report separates
Python planner correctness, raw model reply scores, and final advice scores.
For a longer run interrupted by a rate limit, wait for it to reset and use
`--resume` with the set folder printed by the evaluator.

## Call BudgetAI from Python

`BudgetAI.generate_budget(profile)` builds a proposed monthly budget and returns
Python-calculated totals, allocations, and checked Groq advice. Use
`BudgetAI.respond_to_chat(ChatRequest(...))` for household budgeting questions or
what-if comparisons. Include up to eight recent messages in `history` for
follow-ups. BahayBulsa saves the desktop chat history in its local data folder.

For questions about recorded spending, pass a `SpendingRecords` snapshot of the
relevant dates, categories, and amounts. Month-to-month comparisons use matching
days where possible. A question about last month's total alone uses the full
calendar month.

## Settings (.env)

| Setting | Default | Meaning |
|---|---|---|
| `GROQ_API_KEY` | Required for AI | Backend credential for Groq HTTPS requests. |
| `BUDGETAI_PROVIDER` | `groq` | AI provider. |
| `BUDGETAI_MODEL` | `openai/gpt-oss-20b` | Groq chat model ID. |
| `BUDGETAI_CURRENCY` | `PHP` | Currency for all amounts. |
| `BUDGETAI_TEMPERATURE` | `0` | Answer randomness; 0 = most predictable. |
| `BUDGETAI_TIMEOUT_SECONDS` | `30` | Request timeout in seconds. |

## Security notes

- Keep the key in the Python process environment or the Git-ignored `.env`.
- BahayBulsa manages its records and history in its own storage. An AI request
  can send relevant calculated financial figures, labels, the question, and short
  recent conversation context to Groq over HTTPS. Avoid entering identifying
  details or account credentials in chat.
- Groq retains usage metadata. It ordinarily does not retain inference content,
  but reliability or abuse monitoring can log requests and responses for up to
  30 days, subject to legal obligations. Zero Data Retention is available in
  Groq Console. [Groq data retention](https://console.groq.com/docs/your-data) ·
  [Data Controls](https://console.groq.com/settings/data-controls)
- Budget plan explanations use structured replies and Python validates them.
  Python calculates the budget and checked amounts before the model generates
  advice. [Groq structured outputs](https://console.groq.com/docs/structured-outputs) ·
  [Groq Python SDK](https://github.com/groq/groq-python)
