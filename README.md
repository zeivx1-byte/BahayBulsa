# BahayBulsa
Managing household finances efficiently is a persistent challenge for families balancing budgets and savings goals. The **Household Expense Budget Organizer** centralizes income tracking, categorizes daily expenses, and provides real-time budget analytics. This proposal outlines the platform's core objectives, features, tech stack, and timeline.

## BudgetAI backend

The [BudgetAI API](budgetai/README.md) provides budget planning and budget chat.
The [deployment guide](budgetai/deploy/README.md) covers the GitHub release and
the Linux server that runs the API.

## Run the website and chat locally

Start Ollama and install `qwen3.5:4b-q4_K_M`. Then run these commands from the
BahayBulsa repository root in your Python 3.13 environment.

```sh
python -m pip install -r budgetai/requirements.txt
python -B -m uvicorn --app-dir budgetai browser_preview:app --host 127.0.0.1 --port 8080 --no-access-log
```

Open the [local BahayBulsa website](http://127.0.0.1:8080/preview/). Create a local
account to save your dashboard and recent chat across restarts. Guest entries are
temporary. Add dated transactions, save a monthly limit under **Monthly Budget**,
and open **Chat with BudgetAI**. Use **My dashboard** for your own records and
**Sample household** for examples. **New chat** clears the selected conversation.

Signed-in data is stored in `budgetai/results/bahaybulsa-web.sqlite3`. Set
`BAHAYBULSA_DB_PATH` to use a different SQLite file. Keep that file private and
back it up with SQLite's backup command or while the server is stopped. The
desktop app continues to use its existing `data/transactions.json`.

The website retrieves the signed-in account's current and prior month of posted
transactions, saved limits, and relevant bills and savings goal. Chat can answer
spending questions as soon as you have posted expenses. For trends, add prior
month records. The site saves the eight latest dashboard chat turns and a short
reference for follow-up questions.

The [website AI audit](AI_WEBSITE_AUDIT.md) records the tested browser scenarios,
API changes, measured performance, and remaining limits.

## Try BahayBulsa in GitHub Codespaces

1. [Create a Codespace on the BahayBulsa preview branch](https://codespaces.new/zeivx1-byte/BahayBulsa/tree/codex/bahaybulsa-browser-preview).
2. Wait for setup to install the Python dependencies, download Qwen3.5 4B into Ollama, and start BahayBulsa. The first start takes longer because it downloads the model.
3. In the Codespace, open **Ports** and use **Open in Browser** on **8080 · BahayBulsa preview**. Keep the port **Private**.
4. Open **Chat with BudgetAI** and choose **Sample household** to try the rent example. To test with dated entries, create a BahayBulsa account in that Codespace and choose **My dashboard**.

If port 8080 is missing or the page stops responding after a restart, run
`bash .devcontainer/start-preview.sh` in the Codespace terminal. The script creates
the Python virtual environment when needed and installs dependencies before
starting the website. The model runs inside the Codespace, so its speed depends
on the Codespace's CPU and memory.
Each Codespace has its own website database. Stop the Codespace when finished.
