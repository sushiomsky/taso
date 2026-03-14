"""
TASO – Sandbox test runner.

Runs a project's test suite inside an isolated Docker container and
returns structured results.  Used by the self-improvement engine to
validate patches before deploying them.
"""

from __future__ import annotations

import asyncio
import json
import re
import tempfile
import shutil
from pathlib import Path
from typing import Any, Dict, List, Optional

from sandbox.docker_runner import run_command, ContainerResult
from config.settings import settings
from config.logging_config import self_improvement_log as log


class TestResult:
    def __init__(self, raw: ContainerResult, parser: str) -> None:
        self.raw      = raw
        self.parser   = parser
        self.passed   = 0
        self.failed   = 0
        self.errors   = 0
        self.skipped  = 0
        self.warnings: List[str] = []
        self.failures: List[str] = []
        self._parse()

    @property
    def success(self) -> bool:
        return (
            self.raw.success
            and self.failed  == 0
            and self.errors  == 0
        )

    @property
    def total(self) -> int:
        return self.passed + self.failed + self.errors + self.skipped

    def _parse(self) -> None:
        if self.parser == "pytest":
            self._parse_pytest()
        else:
            self._parse_generic()

    def _parse_pytest(self) -> None:
        text = self.raw.stdout + self.raw.stderr

        # Match:  "5 passed, 2 failed, 1 error in 0.5s"
        m = re.search(
            r"(\d+) passed|(\d+) failed|(\d+) error|(\d+) skipped",
            text,
        )
        # More robust: find all occurrences
        for part in re.findall(r"(\d+) (passed|failed|error(?:s)?|skipped)", text):
            n, kind = int(part[0]), part[1]
            if "pass"  in kind: self.passed  = n
            if "fail"  in kind: self.failed  = n
            if "error" in kind: self.errors  = n
            if "skip"  in kind: self.skipped = n

        # Collect FAILED lines
        for line in text.splitlines():
            if line.startswith("FAILED") or line.startswith("ERROR"):
                self.failures.append(line.strip())

    def _parse_generic(self) -> None:
        self.passed = 0 if self.raw.exit_code != 0 else 1

    def summary(self) -> str:
        return (
            f"{'PASS' if self.success else 'FAIL'} "
            f"– passed={self.passed} failed={self.failed} "
            f"errors={self.errors} skipped={self.skipped}"
        )


async def run_tests(
    repo_path: Path,
    test_command: Optional[List[str]] = None,
    timeout: int = 0,
) -> TestResult:
    """
    Copy *repo_path* into a temporary directory, mount it read-only,
    and run the test suite inside a sandbox container.

    *test_command* defaults to ["python", "-m", "pytest", "--tb=short", "-q"].
    """
    timeout      = timeout or settings.DOCKER_TIMEOUT
    test_command = test_command or ["python", "-m", "pytest", "--tb=short", "-q"]

    # Determine parser from command
    parser = "pytest" if "pytest" in " ".join(test_command) else "generic"

    # Copy repo to a temp dir so we can mount it safely
    with tempfile.TemporaryDirectory(prefix="taso_test_") as tmpdir:
        dest = Path(tmpdir) / "repo"
        shutil.copytree(str(repo_path), str(dest), symlinks=False,
                        ignore=shutil.ignore_patterns(".git", "__pycache__",
                                                       "*.pyc", ".venv"))

        log.info(f"TestRunner: running {test_command} in sandbox")

        result = await run_command(
            command  = test_command,
            workdir  = "/workspace",
            timeout  = timeout,
            mounts   = {str(dest): "/workspace"},
        )

    return TestResult(result, parser)


async def run_static_analysis(repo_path: Path) -> Dict[str, Any]:
    """
    Run bandit inside the sandbox and return structured results.
    """
    with tempfile.TemporaryDirectory(prefix="taso_sa_") as tmpdir:
        dest = Path(tmpdir) / "repo"
        shutil.copytree(str(repo_path), str(dest), symlinks=False,
                        ignore=shutil.ignore_patterns(".git", "__pycache__"))

        result = await run_command(
            command = ["bandit", "-r", "/workspace", "-f", "json", "-q"],
            workdir = "/workspace",
            timeout = 60,
            mounts  = {str(dest): "/workspace"},
        )

    if result.stdout:
        try:
            data = json.loads(result.stdout)
            return {
                "success": True,
                "issues":  data.get("results", []),
                "metrics": data.get("metrics", {}),
            }
        except json.JSONDecodeError:
            pass

    return {
        "success": result.exit_code == 0,
        "raw":     result.stdout[:2000] + result.stderr[:500],
        "issues":  [],
    }
