# BahayBulsa
Managing household finances efficiently is a persistent challenge for families balancing budgets and savings goals. The **Household Expense Budget Organizer** centralizes income tracking, categorizes daily expenses, and provides real-time budget analytics. This proposal outlines the platform's core objectives, features, tech stack, and timeline.

## BudgetAI backend

The [BudgetAI API](budgetai/README.md) provides budget planning and budget chat.
The [deployment guide](budgetai/deploy/README.md) covers the GitHub release and
the Linux server that runs the API.

## Try the website and chat

Open the [browser preview in GitHub Codespaces](https://codespaces.new/zeivx1-byte/BahayBulsa/tree/codex/bahaybulsa-browser-preview)
and create a Codespace. Setup starts the website and downloads the BudgetAI model
on the first run. In **Ports**, open **BahayBulsa preview** on port **8080** and
keep its visibility **Private**. Open **Chat with BudgetAI** and send a message
using the fictional monthly budget shown in the chat panel. Transactions and
bills entered in the preview are cleared when the server stops.

To try it locally, start Ollama and run `ollama pull qwen3.5:2b-q4_K_M`. Then
run these commands from the repository root in your Python 3.13 environment.

```sh
python -m pip install -r budgetai/requirements.txt
python -m uvicorn --app-dir budgetai browser_preview:app --host 127.0.0.1 --port 8080 --no-access-log
```

Open [the local preview](http://127.0.0.1:8080/preview/). Stop the Codespace when
you finish to save your GitHub usage allowance.
