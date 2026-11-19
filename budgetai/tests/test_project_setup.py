"""Phase 2 tests: the project structure and settings are correct.

These tests do not need Ollama, so they run in about a second.
Run them from the BudgetAI_Model folder with:  pytest
"""
import importlib

import pytest

import config

ROOT = config.PROJECT_ROOT

REQUIRED_FOLDERS = ["model", "dataset", "evaluation", "budgeting_engine", "tests"]
REQUIRED_FILES = [
    "model/schemas.py",
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
    assert s.ollama_host.startswith(("http://", "https://"))
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
        ("OLLAMA_HOST", "localhost:11434"),
        ("BUDGETAI_CONTEXT_TOKENS", "100"),
    ],
)
def test_invalid_settings_are_rejected(monkeypatch, name, bad_value):
    monkeypatch.setenv(name, bad_value)
    with pytest.raises(ValueError):
        config.load_settings()
