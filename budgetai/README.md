# BudgetAI Model

A specialized AI budgeting system for households in the Philippines. It runs
on a free, open-weight AI model (Qwen3.5 via Ollama) with no paid AI APIs.

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
| 7. API (FastAPI) | Done locally |
| 8. Online deployment | GitHub release and server setup |

## Folder structure

```
BudgetAI_Model/
├── model/                    AI layer (talks to Qwen through Ollama)
│   ├── schemas.py            data formats: profile in, AI JSON out, checked result
│   ├── budgeting_ai.py       builds the budget with Python, then asks the model to explain it
│   └── instructions.txt      BudgetAI's standing instructions (system prompt)
├── budgeting_engine/         deterministic Python math and rule checks
│   ├── calculations.py       exact facts: totals, deficit, emergency-fund months
│   ├── allocation.py         the budgeting rules; never exceeds income
│   ├── planner.py            the full rule-based plan without the AI (fast)
│   ├── insights.py           plain-language facts for the AI and action steps for the user
│   └── validation.py         missing-data detection and AI advice clean-up
├── dataset/                  412 household scenarios with expected budgets
│   ├── generate_dataset.py   builds the scenarios (same seed = same files)
│   ├── validate_dataset.py   checks every expected budget independently
│   ├── show_scenario.py      prints one scenario
│   ├── budgeting_examples.jsonl   300 scenarios
│   └── test_scenarios.jsonl       112 held-out scenarios (10 hand-checked)
├── evaluation/               regression and external-data challenge cases
│   ├── evaluate_model.py     runs Qwen, saves replies and reports results
│   └── scoring.py            checks raw replies and the final advice separately
├── tests/                    automated tests (run with: pytest)
├── api.py                    local FastAPI service
├── config.py                 reads and checks all settings from .env
├── check_setup.py            confirms Python, Ollama and the model work together
├── try_budgetai.py           runs BudgetAI on four sample households
├── .env                      private settings for this computer (not in Git)
├── .env.example              template for .env (safe to share)
├── requirements.txt          exact Python library versions
├── pytest.ini                test settings
├── .gitignore                files Git must never save (.env, .venv, caches)
└── .gitattributes            consistent line endings on Windows and Linux
```

## Setup on a new Windows computer

Requires Python 3.10+, Git and Ollama.

```powershell
git clone <your-repository-url> BudgetAI_Model
cd BudgetAI_Model
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
ollama pull qwen3.5:2b-q4_K_M
```

## Daily use

```powershell
cd $HOME\Documents\BudgetAI_Model
.\.venv\Scripts\Activate.ps1
python check_setup.py   # Ollama + model + structured answer check
python try_budgetai.py  # BudgetAI on four sample households (uses the model)
python dataset/validate_dataset.py   # check the dataset
pytest                  # automated tests (no Ollama needed)
```

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
python -m evaluation.evaluate_model --set regression heldout challenge --label phase6
```

The evaluator saves case-level replies, model settings, Git and input fingerprints,
scores, and a report for each set under `results/evaluation/`. The report separates
Python planner correctness, Qwen's raw reply scores, and the final advice scores.

The Phase 6 comparison used `qwen3.5:2b-q4_K_M` with scorer 1.1. The first 91
held-out replies were recovered from the saved baseline. The same scorer was
applied to every baseline and final reply. All 137 budgets matched before and
after the changes.

| Cases | Raw replies passing every check | Final advice passing every check |
|---|---|---|
| Named regressions | 0/5 → 0/5 | 2/5 → 5/5 |
| Original held-out | 8/112 → 9/112 | 72/112 → 93/112 |
| External-data challenge | 6/20 → 2/20 | 13/20 → 19/20 |

The held-out rerun had 34 newly passing and 13 newly failing explanations.
The challenge case X-14 failed because its explanation gave an unsupported
amount for a proposed spending cut. Context still matters when checking
figures. For example, the scorer passed R-IRREGULAR-STATED even though the
summary said PHP 14,000 covered a month of PHP 16,000 in required spending.

## Phase 7 local API

With Ollama running and the configured model installed, start the service from
the project folder. Use `python -m pip install -r requirements.txt` in the activated
virtual environment if the API dependencies are missing.

```powershell
python -m uvicorn api:app --host 127.0.0.1 --port 8000
```

The loopback address keeps the service on this computer. Explore the request
and response schemas and try a budget in the [interactive API docs](http://127.0.0.1:8000/docs).

| Endpoint | Result |
|---|---|
| `GET /health` | `200` with `{"status":"ok"}` when the API is running. |
| `POST /budget` | `200` with the budget, verified facts, action steps, and advice. Valid incomplete profiles also return `200` with `missing_data`. |
| `POST /chat` | Ask about a budget or compare a hypothetical expense change. |
| `GET /openapi.json` | The machine-readable API contract. |

Send a sample request from a second PowerShell terminal.

```powershell
$profile = @{
    incomes = @(@{name = 'Salary'; monthly_amount = 30000})
    expenses = @(
        @{name = 'Rent'; category = 'housing'; monthly_amount = 10000}
        @{name = 'Groceries'; category = 'food'; monthly_amount = 8000}
    )
    household_size = 2
}
Invoke-RestMethod -Uri http://127.0.0.1:8000/budget -Method Post `
    -ContentType application/json -Body ($profile | ConvertTo-Json -Depth 5)
```

Malformed JSON and invalid profiles return `422` with field locations and
validation messages. An unavailable Ollama server or model returns `503`.
An unusable model reply or another upstream model failure returns `502`.
Budget responses use `Cache-Control: no-store`.

### Budget chat

In the interactive docs, open **POST /chat**, click **Try it out**, and pick the
budget question or rent increase example. A chat request takes the same household
profile as `/budget`, plus a `message`. The response includes a `reply` and the
current `plan`.

For a comparison, ask “What if my rent goes up by 2,000?” using an expense name
from the profile. You can also send a `change` with `expense_name`, `operation`
(`set`, `increase`, or `decrease`), and `amount`. The separate `scenario.plan`
contains the recalculated budget. Pass the same profile again to keep comparing
with the original. To ask questions about a proposed plan, send the updated
profile with your next question.

For follow-up questions, send up to eight recent messages in `history`. Each
entry has a `role` of `user` or `assistant` and a `content` string. BudgetAI keeps
this history in the request and leaves conversation storage to the calling app.

## Phase 8 deployment

GitHub Actions tests the project and publishes the BudgetAI container to GitHub's
Container Registry. A Linux server runs that image with Ollama and provides
authenticated access over HTTPS. Follow the [Phase 8 deployment guide](deploy/README.md)
to publish an image, start the services, and verify them from another device.

## Settings (.env)

| Setting | Default | Meaning |
|---|---|---|
| `OLLAMA_HOST` | `http://localhost:11434` | Where Ollama runs. localhost = this computer only. |
| `BUDGETAI_MODEL` | `qwen3.5:2b-q4_K_M` | AI model name (must appear in `ollama list`). |
| `BUDGETAI_CURRENCY` | `PHP` | Currency for all amounts. |
| `BUDGETAI_TEMPERATURE` | `0` | Answer randomness; 0 = most predictable. |
| `BUDGETAI_TIMEOUT_SECONDS` | `120` | Seconds to wait for the AI. |
| `BUDGETAI_CONTEXT_TOKENS` | `8192` | How much text the model can read at once (prompt + answer). |

## Security notes

- `.env` is never committed to Git. Put secrets only there.
- Ollama listens on localhost only. Never expose port 11434 to the internet;
  in Phase 8 the public will reach BudgetAI only through the secured API.
