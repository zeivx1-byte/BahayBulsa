"""BudgetAI - Phase 1 setup check.

Confirms that Python, the virtual environment, the libraries, Ollama and the
Qwen model all work together. It only reads information and asks the model
one short question, so it is safe to run at any time.

Run from the BudgetAI_Model folder with the virtual environment active:
    python check_setup.py
"""
from __future__ import annotations

import json
import sys
import time
from contextlib import closing
from typing import TYPE_CHECKING, NoReturn

if TYPE_CHECKING:
    from config import Settings
    from ollama import Client

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


def check_ollama(client: Client, settings: Settings) -> None:
    from httpx import RequestError, TimeoutException
    from ollama import ResponseError

    try:
        listed = client.list()
        ok(f"Ollama is running ({len(listed.models)} model(s) downloaded)")
        client.show(settings.model_name)
    except TimeoutException:
        fail("Ollama timed out while checking the configured model", "Check Ollama and try again.")
    except (ConnectionError, RequestError):
        fail(
            "Cannot reach the configured Ollama server",
            "Check OLLAMA_HOST and make sure Ollama is running.",
        )
    except ResponseError as exc:
        if exc.status_code == 404:
            fail(f"Model {settings.model_name} is unavailable", f"Run ollama pull {settings.model_name}")
        fail(f"Ollama returned HTTP {exc.status_code}", "Check the Ollama server logs and try again.")
    ok(f"Model {settings.model_name} is available")


def check_structured_answer(client: Client, settings: Settings) -> None:
    from httpx import RequestError, TimeoutException
    from ollama import ResponseError
    from pydantic import BaseModel, Field, ValidationError

    class SetupAnswer(BaseModel):
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
            format=SetupAnswer.model_json_schema(),
            options={"temperature": settings.temperature, "num_ctx": settings.context_tokens, "num_predict": 96},
            think=False,
        )
        answer = SetupAnswer.model_validate_json(response.message.content or "")
    except TimeoutException:
        fail(
            f"The model timed out after {settings.timeout_seconds:g} seconds",
            "Check the Ollama logs and adjust BUDGETAI_TIMEOUT_SECONDS if needed.",
        )
    except (ConnectionError, RequestError):
        fail("Lost the connection to Ollama", "Check OLLAMA_HOST and make sure Ollama is running.")
    except ResponseError as exc:
        if exc.status_code == 404:
            fail(f"Model {settings.model_name} is unavailable", f"Run ollama pull {settings.model_name}")
        fail(
            f"Ollama could not run {settings.model_name} (HTTP {exc.status_code})",
            "Check the Ollama server logs and memory usage.",
        )
    except ValidationError:
        fail("The model reply did not match the required JSON format", "Check the model and run setup again.")
    seconds = time.perf_counter() - start
    ok(f"Model returned valid structured JSON in {seconds:.1f} s")
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

    from ollama import Client

    try:
        from config import settings
    except ValueError as exc:
        fail(str(exc), "Review the BudgetAI environment settings and run setup again.")

    with closing(Client(host=settings.ollama_host, timeout=settings.timeout_seconds)) as client:
        check_ollama(client, settings)
        check_structured_answer(client, settings)
    print("\nPhase 1 setup check passed. Your environment is ready.")


if __name__ == "__main__":
    main()
