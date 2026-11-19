"""BudgetAI - Phase 1 setup check.

Confirms that Python, the virtual environment, the libraries, Ollama and the
Qwen model all work together. It only reads information and asks the model
one short question, so it is safe to run at any time.

Run from the BudgetAI_Model folder with the virtual environment active:
    python check_setup.py
"""
import json
import sys
import time

MODEL = "qwen3.5:2b-q4_K_M"
OLLAMA_HOST = "http://localhost:11434"
REQUIRED_LIBRARIES = {
    "ollama": "ollama",
    "pydantic": "pydantic",
    "dotenv": "python-dotenv",
    "pytest": "pytest",
}

# Never crash just because the terminal cannot display a character.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(errors="replace")


def ok(message: str) -> None:
    print(f"[OK]   {message}")


def fail(message: str, fix: str) -> None:
    print(f"[FAIL] {message}")
    print(f"       Fix: {fix}")
    sys.exit(1)


def check_python() -> None:
    version = sys.version.split()[0]
    if sys.version_info < (3, 10):
        fail(f"Python {version} is too old", "Install Python 3.10 or newer.")
    ok(f"Python {version}")

    if sys.prefix == sys.base_prefix:
        fail(
            "The .venv virtual environment is not active",
            r"Run  .\.venv\Scripts\Activate.ps1  in the BudgetAI_Model folder first.",
        )
    ok(f"Virtual environment active: {sys.prefix}")


def check_libraries() -> None:
    missing = []
    for module_name, package_name in REQUIRED_LIBRARIES.items():
        try:
            __import__(module_name)
        except ImportError:
            missing.append(package_name)
    if missing:
        fail(
            f"Missing libraries: {', '.join(missing)}",
            f"Run  pip install {' '.join(missing)}",
        )
    ok(f"Libraries installed: {', '.join(REQUIRED_LIBRARIES.values())}")


def check_ollama(client) -> None:
    try:
        listed = client.list()
    except Exception as exc:  # connection refused, timeout, etc.
        fail(
            f"Cannot reach Ollama at {OLLAMA_HOST} ({exc})",
            "Open Ollama from the Start menu, wait a few seconds, then run this again.",
        )

    names = [getattr(m, "model", None) or getattr(m, "name", "") for m in listed.models]
    ok(f"Ollama is running ({len(names)} model(s) downloaded)")

    if MODEL not in names:
        fail(f"Model {MODEL} is not downloaded", f"Run  ollama pull {MODEL}")
    ok(f"Model {MODEL} is downloaded")


def check_structured_answer(client) -> None:
    from pydantic import BaseModel, ValidationError

    class LeftoverCheck(BaseModel):
        monthly_income: float
        monthly_expenses: float
        amount_left: float
        advice: str

    income, expenses = 25_000, 18_500
    messages = [
        {
            "role": "system",
            "content": (
                "You are BudgetAI, a budgeting assistant for households in the "
                "Philippines. All amounts are in Philippine pesos (PHP). "
                "Reply only with JSON that matches the given schema."
            ),
        },
        {
            "role": "user",
            "content": (
                f"My monthly income is PHP {income:,} and my monthly expenses are "
                f"PHP {expenses:,}. Fill in the JSON with my income, my expenses, "
                "the amount left, and one sentence of advice."
            ),
        },
    ]

    start = time.perf_counter()
    try:
        response = client.chat(
            model=MODEL,
            messages=messages,
            format=LeftoverCheck.model_json_schema(),  # forces this JSON shape
            options={"temperature": 0},                 # same input -> same answer
            think=False,                                # skip slow "thinking"
        )
    except Exception as exc:
        fail(f"The model did not answer ({exc})", "Run  ollama ps  and tell Claude what it shows.")
    seconds = time.perf_counter() - start

    try:
        answer = LeftoverCheck.model_validate_json(response.message.content)
    except ValidationError as exc:
        fail(
            f"The reply was not valid JSON for the schema:\n{exc}",
            "Run the check again. If it keeps failing, send Claude this output.",
        )
    ok(f"Model returned valid structured JSON in {seconds:.1f} s")
    print(json.dumps(answer.model_dump(), indent=2, ensure_ascii=False))

    correct = income - expenses
    if abs(answer.amount_left - correct) < 0.01:
        ok(f"Model's math matches Python: PHP {correct:,.2f} left")
    else:
        print(
            f"[NOTE] The model said PHP {answer.amount_left:,.2f} left, but the correct "
            f"amount is PHP {correct:,.2f}.\n"
            "       Expected with small models. This is why Python, not the AI, "
            "will do all budget math (Phase 5)."
        )


def main() -> None:
    print("BudgetAI - Phase 1 setup check\n")
    check_python()
    check_libraries()

    from ollama import Client

    client = Client(host=OLLAMA_HOST)
    check_ollama(client)
    check_structured_answer(client)
    print("\nPhase 1 setup check passed. Your environment is ready.")


if __name__ == "__main__":
    main()
