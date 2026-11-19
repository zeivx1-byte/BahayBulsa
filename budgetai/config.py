"""Central settings for BudgetAI.

Every part of the project reads its settings from here instead of hard-coding
them. Values come from the .env file in this folder; if a value is missing,
the safe default below is used. Invalid values stop the program with a clear
message instead of causing confusing errors later.
"""
import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent
load_dotenv(PROJECT_ROOT / ".env")

DEFAULTS = {
    "BUDGETAI_PROVIDER": "groq",
    "GROQ_API_KEY": "",
    "BUDGETAI_MODEL": "openai/gpt-oss-20b",
    "BUDGETAI_CURRENCY": "PHP",
    "BUDGETAI_TEMPERATURE": "0",
    "BUDGETAI_TIMEOUT_SECONDS": "30",
}


@dataclass(frozen=True)
class Settings:
    """All BudgetAI settings in one read-only object."""

    provider: str
    groq_api_key: str = field(repr=False)
    model_name: str
    currency: str
    temperature: float
    timeout_seconds: float
    project_root: Path
    instructions_path: Path
    dataset_dir: Path


def _read(name: str) -> str:
    """Return a setting from the environment / .env, or its default."""
    value = os.getenv(name, "").strip()
    return value if value else DEFAULTS[name]


def _read_number(name: str, minimum: float, maximum: float) -> float:
    raw = _read(name)
    try:
        number = float(raw)
    except ValueError:
        raise ValueError(f"{name} must be a number, but the .env file says {raw!r}.") from None
    if not minimum <= number <= maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}, but it is {number}.")
    return number


def load_settings() -> Settings:
    """Read, check and return all settings."""
    provider = _read("BUDGETAI_PROVIDER").lower()
    if provider != "groq":
        raise ValueError("BUDGETAI_PROVIDER must be groq.")

    currency = _read("BUDGETAI_CURRENCY").upper()
    if len(currency) != 3 or not currency.isalpha():
        raise ValueError(f"BUDGETAI_CURRENCY must be a 3-letter code such as PHP, but it is {currency!r}.")

    return Settings(
        provider=provider,
        groq_api_key=_read("GROQ_API_KEY"),
        model_name=_read("BUDGETAI_MODEL"),
        currency=currency,
        temperature=_read_number("BUDGETAI_TEMPERATURE", 0.0, 2.0),
        timeout_seconds=_read_number("BUDGETAI_TIMEOUT_SECONDS", 1.0, 900.0),
        project_root=PROJECT_ROOT,
        instructions_path=PROJECT_ROOT / "model" / "instructions.txt",
        dataset_dir=PROJECT_ROOT / "dataset",
    )


settings = load_settings()
