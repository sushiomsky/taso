"""
Tests for the Model Router and Model Registry.

Covers:
  - classify_task() keyword heuristics
  - ModelRegistry initialization and lookup
  - is_refusal() detection
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from models.model_registry import TaskType, ModelRegistry
from models.model_router import classify_task
from models.ollama_client import is_refusal


# ---------------------------------------------------------------------------
# classify_task heuristics
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("prompt,expected", [
    ("write a python function to parse JSON",   TaskType.CODING),
    ("fix this bug in my class",                TaskType.CODING),
    ("implement a REST API endpoint",           TaskType.CODING),
    ("analyze this vulnerability CVE-2024",    TaskType.SECURITY),
    ("check for injection vulnerability",  TaskType.SECURITY),
    ("research latest threat intelligence",    TaskType.RESEARCH),
    ("what is the OWASP top 10",               TaskType.RESEARCH),
    ("evaluate and summarize these findings",  TaskType.ANALYSIS),
    ("plan the steps for this migration",      TaskType.PLANNING),
])
def test_classify_task_keywords(prompt: str, expected: TaskType):
    result = classify_task(prompt)
    assert result == expected, f"Expected {expected} for {prompt!r}, got {result}"


def test_classify_task_returns_valid_type():
    assert isinstance(classify_task("hello there"), TaskType)


# ---------------------------------------------------------------------------
# is_refusal() detection
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("text,should_be_refusal", [
    ("I can't help with that request.",               True),
    ("I cannot assist with hacking activities.",     True),
    ("I'm sorry, but I won't do that.",              True),
    ("As an AI language model, I must decline.",     True),
    ("I can't help " + "x" * 600,                    False),  # long = never refusal
    ("Here is the Python code you requested:\n```",  False),
    ("The vulnerability CVE-2024-0001 affects...",   False),
    ("",                                              False),
])
def test_is_refusal(text: str, should_be_refusal: bool):
    assert is_refusal(text) == should_be_refusal, f"is_refusal({text[:50]!r})"


# ---------------------------------------------------------------------------
# ModelRegistry
# ---------------------------------------------------------------------------

def test_model_registry_has_models():
    assert len(ModelRegistry().all_models()) > 0


def test_model_registry_coding_models():
    assert len(ModelRegistry().by_task(TaskType.CODING)) > 0


def test_model_registry_preferred_returns_model_or_none():
    preferred = ModelRegistry().preferred_for(TaskType.CODING)
    assert preferred is None or hasattr(preferred, "name")


def test_model_registry_get_known():
    reg = ModelRegistry()
    models = reg.all_models()
    if models:
        assert reg.get(models[0].name) is not None


def test_model_registry_get_unknown_returns_none():
    assert ModelRegistry().get("nonexistent_xyz_abc") is None
