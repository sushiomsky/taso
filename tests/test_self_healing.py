"""
Tests for the Self-Healing pipeline (non-git, non-network parts).

Covers:
  - VersionManager: record, mark_stable, latest_stable, risk_score
  - RiskScorer: score calculation logic
  - TestRunner: syntax_check on valid/invalid Python
  - RollbackManager: error threshold tracking
"""
from __future__ import annotations

import sys
import textwrap
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))


# ---------------------------------------------------------------------------
# VersionManager — already covered in test_memory.py, extend with risk score
# ---------------------------------------------------------------------------

def test_version_risk_score_attached_to_record():
    """After scoring, the record's metadata should contain 'risk_score'."""
    from self_healing.version_manager import VersionManager
    from self_healing.risk_scorer import RiskScorer

    vm = VersionManager()
    rec = vm.record(
        author_agent="dev_agent",
        change_type="patch",
        description="Add new feature",
        files_changed=["agents/new_agent.py", "tests/test_new_agent.py"],
    )
    scorer = RiskScorer()
    score = scorer.score(rec)
    rec.metadata["risk_score"] = score

    assert "risk_score" in rec.metadata
    assert 0.0 <= rec.metadata["risk_score"] <= 10.0


# ---------------------------------------------------------------------------
# RiskScorer tests
# ---------------------------------------------------------------------------

@pytest.fixture
def scorer():
    from self_healing.risk_scorer import RiskScorer
    return RiskScorer()


def _make_record(**kwargs):
    from self_healing.version_manager import VersionManager
    vm = VersionManager()
    return vm.record(
        author_agent=kwargs.get("author_agent", "test"),
        change_type=kwargs.get("change_type", "patch"),
        description=kwargs.get("description", "test change"),
        files_changed=kwargs.get("files_changed", []),
        test_passed=kwargs.get("test_passed", True),
        metadata=kwargs.get("metadata", {}),
    )


def test_risk_score_is_float(scorer):
    rec = _make_record()
    score = scorer.score(rec)
    assert isinstance(score, float)


def test_risk_score_in_range(scorer):
    rec = _make_record(files_changed=["a.py", "b.py"])
    score = scorer.score(rec)
    assert 0.0 <= score <= 10.0


def test_protected_module_increases_risk(scorer):
    safe_rec = _make_record(files_changed=["tools/new_tool.py"])
    risky_rec = _make_record(files_changed=["config/settings.py"])
    assert scorer.score(risky_rec) > scorer.score(safe_rec)


def test_failed_tests_increases_risk(scorer):
    passing = _make_record(test_passed=True)
    failing = _make_record(test_passed=False)
    assert scorer.score(failing) > scorer.score(passing)


def test_many_files_increases_risk(scorer):
    small = _make_record(files_changed=["a.py"])
    large = _make_record(files_changed=[f"file{i}.py" for i in range(20)])
    assert scorer.score(large) > scorer.score(small)


def test_risk_score_with_large_diff_lines(scorer):
    small = _make_record(metadata={"diff_lines": 10})
    large = _make_record(metadata={"diff_lines": 500})
    assert scorer.score(large) > scorer.score(small)


# ---------------------------------------------------------------------------
# TestRunner — syntax_check (no subprocess needed)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_syntax_check_valid_python():
    from self_healing.test_runner import TestRunner
    runner = TestRunner()
    valid_code = textwrap.dedent("""
        def hello(name: str) -> str:
            return f"Hello, {name}!"

        result = hello("world")
    """)
    passed, errors = await runner.syntax_check_code(valid_code)
    assert passed is True
    assert errors == []


@pytest.mark.asyncio
async def test_syntax_check_invalid_python():
    from self_healing.test_runner import TestRunner
    runner = TestRunner()
    invalid_code = "def broken(\n    # missing closing paren"
    passed, errors = await runner.syntax_check_code(invalid_code)
    assert passed is False
    assert len(errors) > 0


@pytest.mark.asyncio
async def test_syntax_check_empty_string():
    from self_healing.test_runner import TestRunner
    runner = TestRunner()
    passed, errors = await runner.syntax_check_code("")
    assert passed is True  # empty string is valid Python


# ---------------------------------------------------------------------------
# RollbackManager — error threshold
# ---------------------------------------------------------------------------

def test_rollback_manager_threshold_not_met():
    from self_healing.rollback_manager import RollbackManager
    rm = RollbackManager(error_threshold=3)
    rm._increment_error("test error 1")
    rm._increment_error("test error 2")
    assert not rm.should_rollback()


def test_rollback_manager_threshold_met():
    from self_healing.rollback_manager import RollbackManager
    rm = RollbackManager(error_threshold=3)
    rm._increment_error("error 1")
    rm._increment_error("error 2")
    rm._increment_error("error 3")
    assert rm.should_rollback()


def test_rollback_manager_reset_after_rollback():
    from self_healing.rollback_manager import RollbackManager
    rm = RollbackManager(error_threshold=2)
    rm._increment_error("e1")
    rm._increment_error("e2")
    assert rm.should_rollback()
    rm.reset()
    assert not rm.should_rollback()
