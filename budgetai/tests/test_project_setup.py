"""Phase 2 tests: the project structure and settings are correct.

These tests run without Groq credentials or network access.
Run them from the BudgetAI_Model folder with:  pytest
"""
import importlib

import pytest

import config

ROOT = config.PROJECT_ROOT

REQUIRED_FOLDERS = ["model", "dataset", "evaluation", "budgeting_engine", "tests"]
REQUIRED_FILES = [
    "model/schemas.py",
    "model/ai_service.py",
    "model/budgeting_ai.py",
    "model/instructions.txt",
    "dataset/budgeting_examples.jsonl",
    "dataset/test_scenarios.jsonl",
    "evaluation/evaluate_model.py",
    "budgeting_engine/calculations.py",
    "budgeting_engine/allocation.py",
    "budgeting_engine/validation.py",
    "try_budgetai.py",
    "requirements.txt",
    "README.md",
    ".env.example",
    ".gitignore",
]


@pytest.mark.parametrize("folder", REQUIRED_FOLDERS)
def test_folder_exists(folder):
    assert (ROOT / folder).is_dir(), f"Missing folder: {folder}"


@pytest.mark.parametrize("path", REQUIRED_FILES)
def test_file_exists(path):
    assert (ROOT / path).is_file(), f"Missing file: {path}"


@pytest.mark.parametrize(
    "module",
    ["model.budgeting_ai", "budgeting_engine.calculations",
     "budgeting_engine.validation", "evaluation.evaluate_model"],
)
def test_module_imports(module):
    importlib.import_module(module)


def test_settings_have_sensible_values():
    s = config.settings
    assert s.model_name, "BUDGETAI_MODEL is empty"
    assert s.provider == "groq"
    assert config.DEFAULTS["BUDGETAI_MODEL"] == "openai/gpt-oss-20b"
    assert len(s.currency) == 3
    assert 0.0 <= s.temperature <= 2.0
    assert s.timeout_seconds > 0


def test_private_files_are_ignored_by_git():
    ignored = (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert ".env" in ignored, ".env must be in .gitignore so settings and secrets are never uploaded"
    assert ".venv/" in ignored, ".venv/ must be in .gitignore"


def test_example_env_lists_every_setting():
    example = (ROOT / ".env.example").read_text(encoding="utf-8")
    for name in config.DEFAULTS:
        assert f"{name}=" in example, f"{name} is missing from .env.example"


@pytest.mark.parametrize(
    "name, bad_value",
    [
        ("BUDGETAI_TEMPERATURE", "hot"),
        ("BUDGETAI_TEMPERATURE", "5"),
        ("BUDGETAI_TIMEOUT_SECONDS", "0"),
        ("BUDGETAI_CURRENCY", "PESO"),
        ("BUDGETAI_PROVIDER", "unknown-provider"),
    ],
)
def test_invalid_settings_are_rejected(monkeypatch, name, bad_value):
    monkeypatch.setenv(name, bad_value)
    with pytest.raises(ValueError):
        config.load_settings()


def test_key_is_read_from_environment_and_omitted_from_repr(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "synthetic-key-for-config-test")
    settings = config.load_settings()
    assert settings.groq_api_key == "synthetic-key-for-config-test"
    assert settings.groq_api_key not in repr(settings)
