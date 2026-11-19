# BahayBulsa website AI audit

## Starting point

The existing website used the same dashboard as the desktop app. It already supported dated transactions, delete and undo, bills, sorting, a sample-household chat, and a dashboard chat backed by one in-memory organizer per browser. The vendored BudgetAI backend used configurable Qwen3.5 4B through Ollama. Python already owned planning and posted-spending arithmetic. Chat kept recent turns in the browser and its account-free dashboard had no saved allocation fields. The desktop application's existing JSON ledger had no account ownership metadata.

## Implementation

- Local accounts use a separate SQLite file. The database stores owner-keyed posted transactions, monthly settings, dashboard state, and the active dashboard conversation. Passwords use individually salted PBKDF2 hashes. The account is resolved from an opaque HttpOnly cookie with a hashed, expiring token on the server. Guest dashboards are scoped to their existing browser cookie. The desktop app continues using its original JSON ledger and UI transport.
- Dashboard chat retrieves an owner- and date-bounded SQLite snapshot using an index on owner and posting date. It reads posted rows and saved allocations for the current and prior months, the reporting month's savings goal, and eligible queued or recurring bills. Posted entries are separate from the bill queue. Python handles up to 5,000 dated ledger entries per request. Qwen sees a compact question-specific subset of verified totals or labels, plus recent turns when needed. This uses structured SQL retrieval.
- Python computes expense and income totals, category shares, month comparisons, budget remaining or overage, goal progress, and changes by named expense. Qwen handles open wording, picks an answer topic and detail level, and helps focus the explanation on relevant facts. The renderer writes the figures and factual claims from the verified data. Common explicit questions and typed follow-ups can skip generation. The model tag and context remain environment-configurable.
- The website stores eight recent turns and a validated subject and period reference per account. The browser submits the current message, and the server loads and updates its own history. A revision check prevents a slow reply from reviving a reset conversation. **New chat** clears website memory; sample-household chat keeps its separate browser thread. Persisted financial values are loaded again on each new answer.
- The existing dashboard and chat display accounts, budget limits, a savings goal, recurring bills, loading feedback, and API errors. These controls use the website's same-origin API and the existing Ollama service. Signing in or out reloads the dashboard and its chat history for that identity.

## Website and API routes

| Route | Change |
|---|---|
| `/preview/` | Existing website dashboard and chat, with new account and budget controls. |
| `GET /api/account`, `POST /api/account/register`, `/api/account/login`, `/api/account/logout` | Local sign-up, sign-in, sign-out, and current account. |
| `GET`/`PUT /api/dashboard/budget` | Month-specific spending limit, category limits, and savings goal. |
| `GET /api/dashboard` and existing dashboard mutations | Use the current account or guest dashboard. Posted expenses remain separate from queued bills. |
| `POST /api/dashboard/bills` | Optional `recurring` flag. |
| `POST /api/preview-chat` | Uses server-owned records and conversation; returns `reply` and a retrieval/answer `Server-Timing` header. Legacy `history` payloads are accepted for compatibility. |
| `GET /api/preview-chat/history`, `POST /api/preview-chat/reset` | Restore or clear the current dashboard conversation. |
| `POST /api/chat`, `POST /api/budget` | Existing planner and sample inputs and responses remain available. |

SQLite creates the `accounts`, `sessions`, `dashboard`, `transactions`, `monthly_preferences`, and `conversations` tables on first account use. The default file is `budgetai/results/bahaybulsa-web.sqlite3`; `BAHAYBULSA_DB_PATH` can point to another local file. Transaction amounts are stored in whole centavos. The separate desktop file has no automatic migration because its rows have no owner IDs.

## Browser verification

The actual `/preview/` website was tested locally with two fictional accounts in an isolated SQLite database. A separate SQL check confirmed the first account's ₱25,600 and second account's ₱850 in current-month posted expenses. The browser tests used the existing chat composer and displayed the replies in place.

| Case | Observed result |
|---|---|
| Current spending | ₱25,600 across November 1–19. The queued bill was excluded. |
| Largest category | Housing, ₱12,500, or 48.8% of recorded expenses. This used the live Qwen route. |
| Food and follow-ups | ₱6,900 in November, ₱5,400 in October, ₱1,500 difference. Groceries accounted for ₱1,000 of the difference. The answer identified the full prior-month comparison. |
| Budget monitoring | ₱600 over a ₱25,000 current-month limit saved through the website's budget form. |
| Advice | Reviewed food first because it was ₱900 over the account's saved food limit. |
| Missing data | The reply said there were no recorded health transactions for the requested period. |
| Account isolation | The second account showed only its ₱850 in expenses and zero inherited chat turns. Signing back in to the first account restored its eight recent turns. After a server restart, the account, ledger, saved limit, and history were restored. |
| Other records | Reported ₱42,000 posted income, the ₱2,800 monthly electricity bill, and ₱5,000 saved toward a ₱20,000 goal. The separate sample chat still recalculated the rent example. |
| Historical budget | October's separate settings saved via the website gave ₱1,900 remaining on its overall limit and a ₱400 food-category overage. |
| Unavailable Ollama | An isolated server with an unreachable Ollama address showed the unavailable-model error, kept the question editable for retry, and preserved earlier chat turns. |

The initial browser run exposed three interpretation problems. A total-only question included an unsolicited previous-month comparison, a posted food question returned a single planned line and lost the follow-up subject, and a recurring bill answer omitted its cadence. The browser rerun verified the corrective routing and rendering. The account adapter also rejects stale authentication instead of saving an edit to a temporary guest ledger.

## Performance on the RTX 3050 Laptop GPU

- Browser-observed elapsed time was 0.27–0.39 seconds for verified totals, follow-ups, and saved-limit checks. A warm Qwen-generated intent route took 6.00 seconds for the recommendation. A cold category request took longer than the browser test tool's 15-second wait; it subsequently displayed the correct reply.
- In a separate live HTTP check, server-side retrieval and projection took 2.5 ms for the spending total and 4.5 ms for an open-language category question. The answer stages took 0.3 ms and 6.23 seconds respectively. These are individual local measurements with the fixture.
- Ollama showed one resident 4B model, 3.7 GB total and a 50% CPU / 50% GPU split at 4,096 tokens. The GPU reported 2,541 MiB in use out of 4,096 MiB, up from a 2 MiB idle snapshot. Windows reported 0.50 GiB available out of 15.70 GiB at that moment; this is whole-system availability, not model-only RAM usage. The website shares the existing Ollama instance.

## Verification coverage and limits

- **Implemented and verified locally.** The browser scenarios above, plus 217 standalone BudgetAI tests and 226 BahayBulsa tests, passed. JavaScript syntax and the mocked UI flow passed. The Python runs reported Starlette's existing `httpx` test-client deprecation warning.
- **Local test scope.** Browser verification covers the local Windows deployment with a small fictional ledger and two accounts. Concurrent update protection was checked by automated tests. Public hosting and sustained concurrent-user load are outside this run.
- **Remaining limits.** The website uses local accounts. The legacy desktop JSON has no owner metadata for safe automatic migration. Chat's standard history window covers the current and previous calendar months, and its factual replies use verified wording templates with Qwen-selected intent and detail. The ledger shows recorded activity. Bank balances and causal explanations for a bill change need additional data. The website currently runs on loopback.

The Windows test workspace used port 8082. From that workspace, the local CMD
command with Ollama open is:

```bat
cd /d "%USERPROFILE%\Documents\BudgetAI_Model\results\bahaybulsa-phase8"
..\..\.venv\Scripts\python.exe -B -m uvicorn --app-dir budgetai browser_preview:app --host 127.0.0.1 --port 8082 --no-access-log
```

## Files changed by this task

- BahayBulsa website and docs: `web/index.html`, `web/app.js`, `web/style.css`, `README.md`, and this audit.
- BahayBulsa account and retrieval backend: `website_store.py`, `budgetai/browser_preview.py`, and `budgetai/website_context.py`.
- BahayBulsa tests: `budgetai/tests/test_browser_preview.py`, `budgetai/tests/test_website_store.py`, and `budgetai/tests/test_ledger_chat.py`.
- BudgetAI core, updated in both checkouts: `model/budget_chat.py`, `model/budgeting_ai.py`, `model/schemas.py`, `model/spending_chat.py`, and `budgeting_engine/spending.py`. In the BahayBulsa checkout these files are under `budgetai/`.
- The standalone BudgetAI checkout also gained `tests/test_ledger_chat.py` and an updated `README.md` account-storage description.

Local backups are under the standalone workspace's ignored `results/website-upgrade/backups/`. Pre-existing uncommitted upgrade changes remain in both checkouts.
