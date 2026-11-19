"""BudgetAI - Phase 1 setup check.

Checks Python, the virtual environment, the libraries, and a structured Groq
reply. The request uses one fictional household example.

Run from the BudgetAI_Model folder with the virtual environment active:
    python check_setup.py
"""
from __future__ import annotations

import json
import sys
import time
from typing import TYPE_CHECKING, NoReturn

if TYPE_CHECKING:
    from config import Settings
    from model.ai_service import GroqAIService

REQUIRED_LIBRARIES = {
    "groq": "groq",
    "pydantic": "pydantic",
    "dotenv": "python-dotenv",
    "pytest": "pytest",
}

# Never crash just because the terminal cannot display a character.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(errors="replace")


def ok(message: str) -> None:
    print(f"[OK]   {message}")


def fail(message: str, fix: str) -> NoReturn:
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
            r"Run .\.venv\Scripts\python.exe check_setup.py in the BudgetAI_Model folder.",
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


def check_structured_answer(client: GroqAIService, settings: Settings) -> None:
    from pydantic import BaseModel, ConfigDict, Field, ValidationError

    class SetupAnswer(BaseModel):
        model_config = ConfigDict(extra="forbid")
        advice: str = Field(min_length=1)

    income, expenses = 25_000, 18_500
    amount_left = income - expenses
    currency = settings.currency
    messages = [
        {
            "role": "system",
            "content": (
                "You are BudgetAI, a budgeting assistant for households in the "
                "Philippines. Reply only with JSON that matches the given schema."
            ),
        },
        {
            "role": "user",
            "content": (
                f"My household has {currency} {amount_left:,} left after monthly expenses. "
                "Give one sentence suggesting a sensible use for some of it."
            ),
        },
    ]

    start = time.perf_counter()
    try:
        response = client.chat(
            model=settings.model_name,
            messages=messages,
            schema=SetupAnswer.model_json_schema(),
            temperature=settings.temperature,
            max_completion_tokens=256,
        )
        answer = SetupAnswer.model_validate_json(response.content)
    except ValidationError:
        fail("The model reply did not match the required JSON format", "Run the setup check again.")
    seconds = time.perf_counter() - start
    ok(f"Groq model {settings.model_name} returned valid structured JSON in {seconds:.1f} s")
    print(json.dumps({
        "monthly_income": income,
        "monthly_expenses": expenses,
        "amount_left": amount_left,
        "advice": answer.advice,
    }, indent=2, ensure_ascii=False))


def main() -> None:
    print("BudgetAI - Phase 1 setup check\n")
    check_python()
    check_libraries()

    try:
        from config import settings
    except ValueError as exc:
        fail(str(exc), "Review the BudgetAI environment settings and run setup again.")

    from model.ai_service import BudgetAIError, GroqAIService

    try:
        with GroqAIService(settings) as client:
            check_structured_answer(client, settings)
    except BudgetAIError as exc:
        fail(str(exc), "Review the Groq settings in README.md, then run the setup check again.")
    print("\nPhase 1 setup check passed. Your environment is ready.")


if __name__ == "__main__":
    main()
