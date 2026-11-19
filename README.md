# BahayBulsa
BahayBulsa is a desktop app for tracking household income, expenses, and bills. BudgetAI uses Groq GPT-OSS 20B for chat and monthly budget recommendations. The built-in Python planner calculates the amounts.

## Run the desktop app on Windows

Get a key from [Groq API Keys](https://console.groq.com/keys). If you do not have `budgetai/.env`, copy `budgetai/.env.example` to that name. Open `budgetai/.env` and set `GROQ_API_KEY` to your key. Existing `.env` files can be edited in place. Keep the file private.

From the BahayBulsa checkout root, run these commands in PowerShell with Python 3.10 or newer.

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r budgetai/requirements.txt pywebview
.\.venv\Scripts\python.exe main.py
```

Open **Chat with BudgetAI** and use **My dashboard** for your entries or **Sample household** to try the assistant. Choose **Generate budget plan** for a plan with Python-calculated allocations and AI recommendations. The dashboard plan estimates monthly allocations from entries posted so far this month. Add dated income and expense entries to improve that estimate. The desktop ledger stores a savings target and has no field for the current savings balance.

Desktop transactions are stored in `data/transactions.json`. Each chat source keeps its eight most recent turns locally in `data/chat_history.json`. **New chat** clears the selected conversation. AI requests can send the question, recent context, and relevant financial figures to Groq. The API key stays in Python, and the full local ledger remains on the device. [Groq data controls](https://console.groq.com/docs/your-data)

## Project layout

- `web/` contains the dashboard and chat interface.
- `main.py` opens the desktop window with PyWebView.
- `desktop_ai.py` connects the desktop app to BudgetAI and saves local chat history.
- `storage.py` loads and saves the desktop transaction ledger.
- `budgetai/` contains the [planner and Groq integration](budgetai/README.md).
