"""
TASO – Dev Lifecycle

Central orchestration of the Autonomous Development & Git Versioning Policy.

Pipeline (per DEVELOPMENT_RULES.md §3):
  sync_repo()            → git fetch + checkout main + pull
  create_feature_branch  → bot/dev/<name>
  run_full_pipeline()    → sync → branch → implement → test → health → risk → commit → push → merge
  commit_to_branch()     → stage + commit on current branch
  merge_to_main()        → merge feature branch → main, delete branch
  auto_rollback()        → revert to last stable on failure

Consumed by:
  - agents/developer_agent.py  — self-improvement loop
  - orchestrator.py            — bootstrap
  - bot/telegram_bot.py        — /dev_lifecycle, /dev_sync commands
"""
from __future__ import annotations

import asyncio
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Dict, List, Optional

from config.logging_config import get_logger
from config.settings import settings
from self_healing.git_manager import (
    git_sync_main, git_create_branch, git_checkout, git_merge,
    git_delete_branch, git_list_branches, git_commit, git_push,
    git_current_branch, git_current_sha, git_create_pr,
)
from self_healing.test_runner import run_pytest, run_smoke_test, run_syntax_check
from self_healing.rollback_manager import rollback_manager
from self_healing.version_manager import version_manager, make_version_id
from self_healing.risk_scorer import RiskScorer
from self_healing.health_checker import health_checker
from self_healing.version_tagger import version_tagger

log = get_logger("dev_lifecycle")

_BRANCH_PREFIX = "bot/dev/"
_risk_scorer   = RiskScorer()


@dataclass
class PipelineResult:
    """Summary of a full dev pipeline run."""
    branch:         str
    description:    str
    success:        bool
    stages:         Dict[str, bool] = field(default_factory=dict)
    commit_sha:     Optional[str]   = None
    pr_url:         Optional[str]   = None
    risk_score:     float           = 0.0
    version_tag:    Optional[str]   = None
    error:          Optional[str]   = None
    started_at:     float           = field(default_factory=time.time)
    finished_at:    float           = 0.0

    def duration(self) -> float:
        end = self.finished_at or time.time()
        return round(end - self.started_at, 1)

    def summary(self) -> str:
        status = "✅ SUCCESS" if self.success else f"❌ FAILED ({self.error or 'unknown'})"
        stage_lines = "\n".join(
            f"  {'✓' if ok else '✗'} {name}" for name, ok in self.stages.items()
        )
        return (
            f"{status}\n"
            f"Branch: {self.branch}\n"
            f"Risk: {self.risk_score:.1f}\n"
            f"Commit: {self.commit_sha or 'none'}\n"
            f"Duration: {self.duration()}s\n"
            f"Stages:\n{stage_lines}"
        )


class DevLifecycle:
    """
    Implements the full Autonomous Development & Git Versioning Policy.

    Usage:
        lc = DevLifecycle()

        # Sync to latest main
        sync = await lc.sync_repo()

        # Run a full automated change
        result = await lc.run_full_pipeline(
            description="Add port scanner improvements",
            change_fn=my_async_change_function,
            change_type="feat",
        )
    """

    def __init__(self) -> None:
        self._current_branch: Optional[str] = None

    # ------------------------------------------------------------------
    # 1. Repository synchronisation
    # ------------------------------------------------------------------

    async def sync_repo(self) -> Dict[str, Any]:
        """
        Full repo sync per policy §1:
          fetch → checkout main → pull → analyse new commits.
        Returns sync summary dict.
        """
        log.info("DevLifecycle: syncing repository to latest main…")
        result = await git_sync_main()

        if result["new_commits"]:
            log.info(
                f"DevLifecycle: {len(result['new_commits'])} new commit(s) pulled:\n"
                + "\n".join(f"  {c['sha']} {c['message']}" for c in result["new_commits"])
            )
        else:
            log.info("DevLifecycle: already up to date.")

        return result

    # ------------------------------------------------------------------
    # 2. Feature branch creation
    # ------------------------------------------------------------------

    async def create_feature_branch(self, feature_name: str) -> str:
        """
        Create and checkout bot/dev/<feature_name>.
        Sanitises the name to be a valid branch identifier.
        Returns the full branch name.
        """
        clean = re.sub(r"[^a-zA-Z0-9_-]", "-", feature_name).strip("-")
        clean = re.sub(r"-{2,}", "-", clean)[:50]
        branch = f"{_BRANCH_PREFIX}{clean}"

        # If branch already exists, append timestamp
        existing = await git_list_branches()
        if branch in existing:
            suffix = datetime.now(tz=timezone.utc).strftime("%H%M%S")
            branch = f"{branch}-{suffix}"

        ok = await git_create_branch(branch)
        if ok:
            self._current_branch = branch
            log.info(f"DevLifecycle: created branch '{branch}'")
        else:
            log.error(f"DevLifecycle: failed to create branch '{branch}'")
        return branch

    # ------------------------------------------------------------------
    # 3. Full pipeline
    # ------------------------------------------------------------------

    async def run_full_pipeline(
        self,
        description: str,
        change_fn: Callable[[], Awaitable[List[str]]],
        change_type: str = "feat",
        bump: str = "patch",
        author_agent: str = "developer",
        auto_merge: bool = True,
    ) -> PipelineResult:
        """
        Execute the complete dev lifecycle pipeline:

          sync → branch → implement → syntax → tests → health → risk → commit → push → merge

        Args:
            description:  Human-readable description of the change.
            change_fn:    Async callable that implements the change.
                          Must return list of modified file paths.
            change_type:  Git conventional commit type (feat/fix/refactor/…).
            bump:         Version bump type: patch | minor | major.
            author_agent: Agent performing the change.
            auto_merge:   Merge to main after all gates pass.

        Returns PipelineResult with full audit trail.
        """
        feature_name = re.sub(r"\s+", "-", description.lower())[:40]
        result = PipelineResult(branch="", description=description, success=False)

        try:
            # ── Stage 1: Sync ────────────────────────────────────────────
            sync = await self.sync_repo()
            result.stages["sync"] = sync["success"]
            if not sync["success"]:
                log.warning("DevLifecycle: sync failed — continuing with local state")

            # ── Stage 2: Feature branch ──────────────────────────────────
            branch = await self.create_feature_branch(feature_name)
            result.branch = branch
            result.stages["branch"] = bool(branch)
            if not branch:
                result.error = "Branch creation failed"
                return self._finish(result)

            # ── Stage 3: Implement change ────────────────────────────────
            try:
                changed_files = await change_fn()
                changed_files = list(changed_files or [])
            except Exception as exc:
                log.error(f"DevLifecycle: change_fn raised: {exc}")
                result.stages["implement"] = False
                result.error = f"Implementation failed: {exc}"
                await self._cleanup_branch(branch)
                return self._finish(result)
            result.stages["implement"] = True

            # ── Stage 4: Static analysis ─────────────────────────────────
            syntax_ok, syntax_out = await run_syntax_check(
                changed_files if changed_files else None
            )
            result.stages["syntax"] = syntax_ok
            if not syntax_ok:
                log.error(f"DevLifecycle: syntax errors:\n{syntax_out[:300]}")
                result.error = "Syntax check failed"
                await self._cleanup_branch(branch)
                return self._finish(result)

            # ── Stage 5: Test suite ──────────────────────────────────────
            tests_ok, test_out = await run_pytest()
            result.stages["tests"] = tests_ok
            if not tests_ok:
                log.error(f"DevLifecycle: tests failed:\n{test_out[-500:]}")
                result.error = "Test suite failed"
                await self._cleanup_branch(branch)
                return self._finish(result)

            # ── Stage 6: Health checks ───────────────────────────────────
            health = await health_checker.check_all()
            result.stages["health"] = health.passed
            if not health.passed:
                log.error(f"DevLifecycle: health checks failed: {health.failed_checks}")
                result.error = f"Health check failed: {health.failed_checks}"
                await self._cleanup_branch(branch)
                return self._finish(result)

            # ── Stage 7: Risk scoring ────────────────────────────────────
            version_id = make_version_id()
            ver_record = version_manager.record(
                author_agent=author_agent,
                change_type=change_type,
                description=description,
                files_changed=changed_files,
                test_passed=True,
            )
            risk = _risk_scorer.score(ver_record)
            result.risk_score = risk
            result.stages["risk"] = risk < 8.0

            if risk >= 8.0:
                log.warning(f"DevLifecycle: CRITICAL risk score {risk:.1f} — blocking commit")
                result.error = f"Risk score {risk:.1f} exceeds CRITICAL threshold (8.0)"
                await self._cleanup_branch(branch)
                return self._finish(result)

            # ── Stage 8: Commit ──────────────────────────────────────────
            commit_msg = self._build_commit_msg(
                change_type, description, changed_files, test_out
            )
            sha = await git_commit(commit_msg, version_id)
            result.stages["commit"] = bool(sha)
            result.commit_sha = sha
            if not sha:
                result.error = "Commit failed"
                await self._cleanup_branch(branch)
                return self._finish(result)

            # Mark version record as committed
            ver_record.commit_sha = sha
            ver_record.test_passed = True

            # ── Stage 9: Push ────────────────────────────────────────────
            push_ok = await git_push(branch=branch, tags=False)
            result.stages["push"] = push_ok
            if not push_ok:
                log.warning("DevLifecycle: push failed — change committed locally")

            # ── Stage 10: Merge / PR ─────────────────────────────────────
            if auto_merge:
                merged = await self.merge_to_main(branch, delete_after=True)
                result.stages["merge"] = merged
                if merged:
                    # Tag the stable version
                    tag = await version_tagger.tag_stable(
                        bump=bump,
                        message=f"{change_type}: {description[:60]}",
                    )
                    result.version_tag = tag
                    ver_record.stable = True
                    ver_record.deployed = True
                    version_manager.mark_stable(ver_record.version_id)
            else:
                # Create PR instead of direct merge
                pr_url = await git_create_pr(
                    source_branch=branch,
                    title=f"{change_type}: {description[:72]}",
                    body=self._build_pr_body(result, test_out),
                )
                result.pr_url = pr_url
                result.stages["pr_created"] = bool(pr_url)

            result.success = True
            log.info(f"DevLifecycle: pipeline succeeded — {sha[:12] if sha else 'n/a'}")

        except Exception as exc:
            log.exception("DevLifecycle: unexpected pipeline error")
            result.error = str(exc)
            result.success = False

        return self._finish(result)

    # ------------------------------------------------------------------
    # 4. Merge helpers
    # ------------------------------------------------------------------

    async def merge_to_main(
        self, branch: str, delete_after: bool = True
    ) -> bool:
        """Merge branch into main and optionally delete the feature branch."""
        ok = await git_merge(branch, "main", no_ff=True)
        if ok:
            push_ok = await git_push(branch="main", tags=True)
            if delete_after:
                await git_delete_branch(branch, force=True)
            log.info(f"DevLifecycle: merged '{branch}' → main (push={push_ok})")
        return ok

    # ------------------------------------------------------------------
    # 5. Auto-rollback
    # ------------------------------------------------------------------

    async def auto_rollback(self, reason: str) -> Optional[str]:
        """
        Trigger rollback to last stable version and return to main.
        Returns SHA rolled back to, or None on failure.
        """
        log.warning(f"DevLifecycle: auto_rollback triggered — {reason}")
        sha = await rollback_manager.rollback(reason=reason)
        if sha:
            await git_checkout("main")
        return sha

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    async def _cleanup_branch(self, branch: str) -> None:
        """Return to main and delete the failed feature branch."""
        try:
            await git_checkout("main")
            await git_delete_branch(branch, force=True)
            log.info(f"DevLifecycle: cleaned up failed branch '{branch}'")
        except Exception as exc:
            log.warning(f"DevLifecycle: cleanup failed: {exc}")

    @staticmethod
    def _build_commit_msg(
        change_type: str,
        description: str,
        files_changed: List[str],
        test_out: str,
    ) -> str:
        files_str = ", ".join(files_changed[:5]) or "various"
        passed = "passed" if "passed" in test_out.lower() else "completed"
        return (
            f"{change_type}: {description}\n\n"
            f"Files changed: {files_str}\n"
            f"Tests: {passed}\n"
            f"Automated commit by TASO DevLifecycle\n\n"
            "Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>"
        )

    @staticmethod
    def _build_pr_body(result: PipelineResult, test_out: str) -> str:
        stages = "\n".join(
            f"- {'✅' if ok else '❌'} {name}"
            for name, ok in result.stages.items()
        )
        return (
            f"## {result.description}\n\n"
            f"**Risk score:** {result.risk_score:.1f}\n"
            f"**Duration:** {result.duration()}s\n\n"
            f"### Pipeline stages\n{stages}\n\n"
            f"### Test output\n```\n{test_out[-800:]}\n```\n\n"
            "*Automated PR by TASO DevLifecycle*"
        )

    @staticmethod
    def _finish(result: PipelineResult) -> PipelineResult:
        result.finished_at = time.time()
        return result


# Module-level singleton
dev_lifecycle = DevLifecycle()
