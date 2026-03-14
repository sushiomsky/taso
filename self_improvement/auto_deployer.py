"""
TASO – Self-improvement: auto_deployer

Safely applies validated patches through a multi-gate pipeline:

  Gate 1 – Protection check     (no protected modules touched)
  Gate 2 – Patch size limit      (< MAX_PATCH_LINES)
  Gate 3 – Git apply --check     (patch is syntactically valid)
  Gate 4 – Test suite            (must pass inside sandbox)
  Gate 5 – Static analysis       (score must not worsen)
  Gate 6 – Audit log             (always written regardless of outcome)

Only if ALL gates pass is the patch committed to the repository.
"""

from __future__ import annotations

import asyncio
import shutil
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from config.settings import settings
from config.logging_config import self_improvement_log as log
from self_improvement.code_analyzer import CodeAnalyzer
from self_improvement.patch_generator import PatchProposal
from sandbox.test_runner import run_tests, run_static_analysis


@dataclass
class DeploymentResult:
    proposal:    PatchProposal
    gate_results: Dict[str, Any]  = field(default_factory=dict)
    deployed:    bool             = False
    reason:      str              = ""
    commit_hash: str              = ""
    ts:          str              = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    def summary(self) -> str:
        status = "✅ DEPLOYED" if self.deployed else f"❌ REJECTED ({self.reason})"
        gates  = " | ".join(
            f"{k}: {'✓' if v.get('pass') else '✗'}"
            for k, v in self.gate_results.items()
        )
        return f"{status}\nGates: {gates}"


class AutoDeployer:
    """
    Evaluates a PatchProposal through all safety gates and, if they all
    pass, applies the patch and commits to the repository.
    """

    def __init__(self, audit_callable=None) -> None:
        """
        *audit_callable* is an async function(actor, action, target, status, detail)
        connected to the knowledge database audit log.
        """
        self._audit    = audit_callable
        self._analyser = CodeAnalyzer()

    async def evaluate_and_deploy(
        self, proposal: PatchProposal
    ) -> DeploymentResult:
        result = DeploymentResult(proposal=proposal)

        log.info(f"AutoDeployer: evaluating patch for {proposal.file_path}")

        # --- Gate 1: protected module check ----------------------------
        ok, reason = self._gate_protection(proposal)
        result.gate_results["protection"] = {"pass": ok, "detail": reason}
        if not ok:
            return await self._reject(result, f"protected module: {reason}")

        # --- Gate 2: patch size ----------------------------------------
        ok, reason = self._gate_size(proposal)
        result.gate_results["patch_size"] = {"pass": ok, "detail": reason}
        if not ok:
            return await self._reject(result, reason)

        # --- Gate 3: git apply --check ----------------------------------
        ok, reason = await self._gate_git_check(proposal)
        result.gate_results["git_apply"] = {"pass": ok, "detail": reason}
        if not ok:
            return await self._reject(result, f"git apply failed: {reason}")

        # --- Gate 4: test suite ----------------------------------------
        ok, reason = await self._gate_tests(proposal)
        result.gate_results["tests"] = {"pass": ok, "detail": reason}
        if not ok:
            return await self._reject(result, f"tests failed: {reason}")

        # --- Gate 5: static analysis ------------------------------------
        ok, reason = await self._gate_static_analysis(proposal)
        result.gate_results["static_analysis"] = {"pass": ok, "detail": reason}
        if not ok:
            return await self._reject(result, f"static analysis regressed: {reason}")

        # --- All gates passed – apply patch ----------------------------
        ok, commit_hash, err = await self._apply_patch(proposal)
        if not ok:
            return await self._reject(result, f"patch apply error: {err}")

        result.deployed    = True
        result.commit_hash = commit_hash
        log.success(f"AutoDeployer: patch deployed for {proposal.file_path} [{commit_hash}]")

        await self._write_audit(result)
        return result

    # ------------------------------------------------------------------
    # Gates
    # ------------------------------------------------------------------

    @staticmethod
    def _gate_protection(proposal: PatchProposal):
        for module in settings.PROTECTED_MODULES:
            if module in proposal.file_path:
                return False, proposal.file_path
        return True, ""

    @staticmethod
    def _gate_size(proposal: PatchProposal):
        if proposal.patch_size > settings.MAX_PATCH_LINES:
            return (
                False,
                f"{proposal.patch_size} lines > limit {settings.MAX_PATCH_LINES}",
            )
        return True, f"{proposal.patch_size} lines"

    async def _gate_git_check(self, proposal: PatchProposal):
        import asyncio, tempfile, os
        with tempfile.NamedTemporaryFile(
            suffix=".patch", mode="w", delete=False
        ) as fh:
            fh.write(proposal.patch)
            pfile = fh.name
        try:
            proc = await asyncio.create_subprocess_exec(
                "git", "-C", str(settings.GIT_REPO_PATH),
                "apply", "--check", pfile,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            _, stderr = await asyncio.wait_for(proc.communicate(), timeout=15)
            ok = proc.returncode == 0
            return ok, stderr.decode().strip()
        except Exception as exc:
            return False, str(exc)
        finally:
            os.unlink(pfile)

    async def _gate_tests(self, proposal: PatchProposal):
        """Apply patch to a temp copy, run tests inside sandbox."""
        with tempfile.TemporaryDirectory(prefix="taso_deploy_") as tmpdir:
            dest = Path(tmpdir) / "repo"
            shutil.copytree(
                str(settings.GIT_REPO_PATH), str(dest),
                ignore=shutil.ignore_patterns(".git", "__pycache__", "*.pyc"),
            )

            # Write modified file
            target = dest / Path(proposal.file_path).relative_to(
                settings.GIT_REPO_PATH
            )
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(proposal.modified)

            test_result = await run_tests(dest, timeout=120)
            ok          = test_result.success
            return ok, test_result.summary()

    async def _gate_static_analysis(self, proposal: PatchProposal):
        """Score must not worsen after patch is applied."""
        original_result = self._analyser.analyse_file(
            Path(proposal.file_path)
        )
        original_score = original_result.get("score", 10.0)

        modified_analysis = self._analyser.analyse_file(
            _write_temp(proposal.modified)
        )
        modified_score = modified_analysis.get("score", 0.0)

        ok = modified_score >= original_score - 0.5  # allow tiny tolerance
        return ok, f"before={original_score} after={modified_score}"

    # ------------------------------------------------------------------
    # Apply patch
    # ------------------------------------------------------------------

    async def _apply_patch(
        self, proposal: PatchProposal
    ) -> tuple[bool, str, str]:
        import os, tempfile

        with tempfile.NamedTemporaryFile(
            suffix=".patch", mode="w", delete=False
        ) as fh:
            fh.write(proposal.patch)
            pfile = fh.name

        try:
            proc = await asyncio.create_subprocess_exec(
                "git", "-C", str(settings.GIT_REPO_PATH),
                "apply", pfile,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            _, stderr = await asyncio.wait_for(proc.communicate(), timeout=15)
            if proc.returncode != 0:
                return False, "", stderr.decode().strip()

            # Commit
            hash_proc = await asyncio.create_subprocess_exec(
                "git", "-C", str(settings.GIT_REPO_PATH),
                "add", "-A",
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await hash_proc.wait()

            commit_proc = await asyncio.create_subprocess_exec(
                "git", "-C", str(settings.GIT_REPO_PATH),
                "commit", "-m",
                f"[TASO Auto-Improve] {proposal.description}",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            cout, _ = await commit_proc.wait(), None
            # Get last commit hash
            h_proc = await asyncio.create_subprocess_exec(
                "git", "-C", str(settings.GIT_REPO_PATH),
                "rev-parse", "--short", "HEAD",
                stdout=asyncio.subprocess.PIPE,
            )
            h_out, _ = await h_proc.communicate()
            return True, h_out.decode().strip(), ""
        except Exception as exc:
            return False, "", str(exc)
        finally:
            os.unlink(pfile)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    async def _reject(
        self, result: DeploymentResult, reason: str
    ) -> DeploymentResult:
        result.deployed = False
        result.reason   = reason
        log.warning(f"AutoDeployer: patch rejected – {reason}")
        await self._write_audit(result)
        return result

    async def _write_audit(self, result: DeploymentResult) -> None:
        if self._audit:
            await self._audit(
                actor  = "auto_deployer",
                action = "deploy_patch" if result.deployed else "reject_patch",
                target = result.proposal.file_path,
                status = "ok" if result.deployed else "rejected",
                detail = {
                    "gates":       result.gate_results,
                    "patch_lines": result.proposal.patch_size,
                    "reason":      result.reason,
                    "commit":      result.commit_hash,
                },
            )


def _write_temp(code: str) -> Path:
    """Write code to a temp file and return its path."""
    import tempfile
    fh = tempfile.NamedTemporaryFile(
        suffix=".py", mode="w", delete=False, prefix="taso_sa_"
    )
    fh.write(code)
    fh.close()
    return Path(fh.name)
