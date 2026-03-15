"""
TASO – Risk Scorer

Calculates a risk score (0.0–10.0) for a proposed code change before
it is committed and pushed. Higher score = more risky.

Factors considered:
  - Number of files changed             (more files → higher risk)
  - Patch size in diff lines            (larger diff → higher risk)
  - Protected module involvement        (config/, sandbox/ → high risk)
  - Test coverage (tests pass/fail)     (failing tests → highest risk)
  - Change type                         (config changes riskier than tools)
  - Author agent trustworthiness        (automated agents slightly riskier)

Score bands:
  0.0–2.9   LOW      — safe to auto-deploy
  3.0–5.9   MEDIUM   — deploy with logging
  6.0–7.9   HIGH     — require human approval or extra tests
  8.0–10.0  CRITICAL — block auto-deploy; alert admin
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from config.logging_config import get_logger
from config.settings import settings

if TYPE_CHECKING:
    from self_healing.version_manager import VersionRecord

log = get_logger("risk_scorer")

# Change types ordered by inherent risk (higher index → higher risk)
_CHANGE_TYPE_RISK: dict[str, float] = {
    "tool_add":     0.5,
    "tool_update":  1.0,
    "agent_add":    1.5,
    "patch":        2.0,
    "config":       3.5,
}

# Agents with elevated trust reduce the risk slightly
_TRUSTED_AGENTS = {"dev_agent", "developer", "self_healing"}


class RiskScorer:
    """
    Stateless risk calculator for VersionRecord objects.
    Can also score raw parameters without a VersionRecord.
    """

    def score(self, record: "VersionRecord") -> float:
        """
        Calculate a risk score for *record*.
        Returns a float in [0.0, 10.0].
        """
        s = 0.0

        # ── Change type base risk ───────────────────────────────────────
        s += _CHANGE_TYPE_RISK.get(record.change_type, 2.0)

        # ── Number of files changed ─────────────────────────────────────
        n_files = len(record.files_changed)
        if n_files == 0:
            s += 0.0
        elif n_files <= 2:
            s += 0.5
        elif n_files <= 5:
            s += 1.0
        elif n_files <= 10:
            s += 2.0
        else:
            s += 3.0

        # ── Protected module involvement ────────────────────────────────
        protected = settings.PROTECTED_MODULES  # e.g. ["config", "sandbox"]
        for path in record.files_changed:
            for mod in protected:
                if mod in path:
                    s += 2.0
                    log.debug(f"RiskScorer: +2.0 for protected module path '{path}'")
                    break  # only penalise once per file

        # ── Diff size ───────────────────────────────────────────────────
        diff_lines = record.metadata.get("diff_lines", 0)
        if diff_lines > 400:
            s += 2.0
        elif diff_lines > 200:
            s += 1.0
        elif diff_lines > 80:
            s += 0.5

        # ── Test result ─────────────────────────────────────────────────
        if not record.test_passed:
            s += 3.0  # major penalty for untested code

        # ── Author agent trust ──────────────────────────────────────────
        if record.author_agent not in _TRUSTED_AGENTS:
            s += 0.5

        # ── Clamp to [0.0, 10.0] ────────────────────────────────────────
        score = round(min(max(s, 0.0), 10.0), 2)
        log.info(
            f"RiskScorer: version={record.version_id} "
            f"type={record.change_type} files={n_files} "
            f"diff_lines={diff_lines} tests_pass={record.test_passed} "
            f"→ score={score}"
        )
        return score

    def band(self, score: float) -> str:
        """Return a human-readable risk band label."""
        if score < 3.0:
            return "LOW"
        elif score < 6.0:
            return "MEDIUM"
        elif score < 8.0:
            return "HIGH"
        return "CRITICAL"

    def is_safe_to_deploy(self, score: float, max_score: float = 6.0) -> bool:
        """Return True if score is below the deployment threshold."""
        return score < max_score


# Module-level singleton
risk_scorer = RiskScorer()
