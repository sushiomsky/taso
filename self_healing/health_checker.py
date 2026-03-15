"""
TASO – Health Checker

Runtime validation of all critical system components.
Called before every commit in the DevLifecycle pipeline and on startup.

Checks (per DEVELOPMENT_RULES.md §6):
  check_imports   – critical modules importable
  check_tools     – ToolRegistry loads ≥ 1 static tool
  check_memory    – KnowledgeDB connects, VectorStore initialises
  check_sandbox   – subprocess sandbox executes a no-op tool
  check_telegram  – Telegram token responds to getMe
  check_agents    – all registered agent classes instantiate

Usage:
    from self_healing.health_checker import health_checker
    report = await health_checker.check_all()
    if not report.passed:
        print(report.summary())
"""
from __future__ import annotations

import asyncio
import sys
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from config.logging_config import get_logger
from config.settings import settings

log = get_logger("health_checker")

# Modules that MUST be importable for the system to function
_CRITICAL_MODULES = [
    "config.settings",
    "config.logging_config",
    "agents.message_bus",
    "agents.base_agent",
    "tools.base_tool",
    "memory.knowledge_db",
    "memory.vector_store",
    "self_healing.git_manager",
]

# Agent classes to spot-check
_AGENT_CLASSES = [
    ("agents.coordinator_agent",  "CoordinatorAgent"),
    ("agents.security_agent",     "SecurityAnalysisAgent"),
    ("agents.developer_agent",    "DeveloperAgent"),
    ("agents.memory_agent",       "MemoryAgent"),
    ("agents.planner_agent",      "PlannerAgent"),
]


@dataclass
class CheckResult:
    name:    str
    passed:  bool
    detail:  str = ""
    elapsed: float = 0.0


@dataclass
class HealthReport:
    checks:      List[CheckResult] = field(default_factory=list)
    started_at:  float = field(default_factory=time.time)
    finished_at: float = 0.0

    @property
    def passed(self) -> bool:
        """True only if all critical checks pass."""
        return all(c.passed for c in self.checks if not c.name.startswith("optional"))

    @property
    def failed_checks(self) -> List[str]:
        return [c.name for c in self.checks if not c.passed]

    @property
    def passed_checks(self) -> List[str]:
        return [c.name for c in self.checks if c.passed]

    def duration(self) -> float:
        end = self.finished_at or time.time()
        return round(end - self.started_at, 2)

    def summary(self) -> str:
        lines = [
            f"{'✅' if c.passed else '❌'} {c.name} ({c.elapsed:.2f}s)"
            + (f" — {c.detail[:80]}" if c.detail else "")
            for c in self.checks
        ]
        status = "HEALTHY" if self.passed else f"UNHEALTHY ({len(self.failed_checks)} failed)"
        return (
            f"Health Check: {status} | {self.duration()}s\n"
            + "\n".join(lines)
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "passed":   self.passed,
            "duration": self.duration(),
            "checks":   [
                {"name": c.name, "passed": c.passed, "detail": c.detail}
                for c in self.checks
            ],
        }


class HealthChecker:
    """Runs all health checks and returns a HealthReport."""

    async def check_all(self, quick: bool = False) -> HealthReport:
        """
        Run all health checks concurrently.
        If quick=True, skip the Telegram API call.
        """
        report = HealthReport()

        tasks = [
            self.check_imports(),
            self.check_tools(),
            self.check_memory(),
            self.check_sandbox(),
            self.check_agents(),
        ]
        if not quick:
            tasks.append(self.check_telegram())

        results = await asyncio.gather(*tasks, return_exceptions=True)

        for r in results:
            if isinstance(r, Exception):
                report.checks.append(CheckResult(
                    name="unknown",
                    passed=False,
                    detail=str(r),
                ))
            elif isinstance(r, CheckResult):
                report.checks.append(r)

        report.finished_at = time.time()
        log.info(f"HealthChecker: {report.summary()}")
        return report

    # ------------------------------------------------------------------
    # Individual checks
    # ------------------------------------------------------------------

    async def check_imports(self) -> CheckResult:
        """Verify all critical modules are importable."""
        t0 = time.time()
        failed: List[str] = []

        code = (
            "import sys\n"
            f"sys.path.insert(0, {repr(str(settings.GIT_REPO_PATH))})\n"
            "errors = []\n"
            "modules = " + repr(_CRITICAL_MODULES) + "\n"
            "for m in modules:\n"
            "    try:\n"
            "        __import__(m)\n"
            "    except Exception as e:\n"
            "        errors.append(f'{m}: {e}')\n"
            "if errors:\n"
            "    print('\\n'.join(errors)); __import__('sys').exit(1)\n"
            "else:\n"
            "    print('OK')\n"
        )
        proc = await asyncio.create_subprocess_exec(
            sys.executable, "-c", code,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=20)
        except asyncio.TimeoutError:
            proc.kill()
            return CheckResult("imports", False, "timeout", time.time() - t0)

        out = (stdout + stderr).decode(errors="replace").strip()
        passed = proc.returncode == 0
        return CheckResult("imports", passed, "" if passed else out[:200], time.time() - t0)

    async def check_tools(self) -> CheckResult:
        """Verify ToolRegistry discovers at least 1 static tool."""
        t0 = time.time()
        try:
            from tools.base_tool import ToolRegistry
            r = ToolRegistry()
            r.discover()
            n = len(r._tools)
            return CheckResult("tools", n >= 1, f"{n} tools loaded", time.time() - t0)
        except Exception as exc:
            return CheckResult("tools", False, str(exc)[:200], time.time() - t0)

    async def check_memory(self) -> CheckResult:
        """Verify KnowledgeDB connects and VectorStore initialises."""
        t0 = time.time()
        try:
            from memory.knowledge_db import KnowledgeDB
            from memory.vector_store import VectorStore, _FAISS_OK

            db = KnowledgeDB()
            await db.connect()
            await db.close()

            vs = VectorStore()
            vs.load()

            detail = f"DB OK | VectorStore {'full' if _FAISS_OK else 'degraded'}"
            return CheckResult("memory", True, detail, time.time() - t0)
        except Exception as exc:
            return CheckResult("memory", False, str(exc)[:200], time.time() - t0)

    async def check_sandbox(self) -> CheckResult:
        """Verify the subprocess sandbox can execute a trivial tool."""
        t0 = time.time()
        try:
            from tools.sandbox_tester import sandbox_test_tool

            code = "def run_tool(i): return {'success': True, 'result': 'ok', 'error': None}"
            passed, output = await sandbox_test_tool(code, {}, timeout=10)
            return CheckResult(
                "sandbox", passed,
                output[:100] if not passed else "subprocess OK",
                time.time() - t0,
            )
        except Exception as exc:
            return CheckResult("sandbox", False, str(exc)[:200], time.time() - t0)

    async def check_telegram(self) -> CheckResult:
        """Verify the Telegram bot token responds to getMe."""
        t0 = time.time()
        if not settings.TELEGRAM_BOT_TOKEN:
            return CheckResult(
                "optional.telegram", True, "no token configured", time.time() - t0
            )
        try:
            import aiohttp
            url = f"https://api.telegram.org/bot{settings.TELEGRAM_BOT_TOKEN}/getMe"
            async with aiohttp.ClientSession() as sess:
                async with sess.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                    data = await resp.json()
                    ok   = data.get("ok", False)
                    name = data.get("result", {}).get("username", "?") if ok else "?"
                    return CheckResult(
                        "telegram", ok,
                        f"@{name}" if ok else str(data.get("description", "error")),
                        time.time() - t0,
                    )
        except Exception as exc:
            return CheckResult("telegram", False, str(exc)[:200], time.time() - t0)

    async def check_agents(self) -> CheckResult:
        """Verify all core agent classes instantiate cleanly."""
        t0 = time.time()
        failed: List[str] = []
        try:
            from unittest.mock import MagicMock
            from agents.message_bus import MessageBus

            mock_bus = MagicMock(spec=MessageBus)
            mock_bus.subscribe = MagicMock()

            for module_name, class_name in _AGENT_CLASSES:
                try:
                    import importlib
                    mod = importlib.import_module(module_name)
                    cls = getattr(mod, class_name)
                    if class_name == "MemoryAgent":
                        # MemoryAgent requires db, vs, conv params
                        from unittest.mock import AsyncMock
                        cls(mock_bus, AsyncMock(), MagicMock(), MagicMock())
                    else:
                        cls(mock_bus)
                except Exception as exc:
                    failed.append(f"{class_name}: {exc}")

            passed = len(failed) == 0
            detail = (
                f"{len(_AGENT_CLASSES)} agents OK" if passed
                else "; ".join(failed[:3])
            )
            return CheckResult("agents", passed, detail, time.time() - t0)
        except Exception as exc:
            return CheckResult("agents", False, str(exc)[:200], time.time() - t0)


# Module-level singleton
health_checker = HealthChecker()
