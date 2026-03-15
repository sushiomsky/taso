"""
TASO – SecurityAnalysisAgent

Performs static code analysis, vulnerability detection, and security
scanning of repositories and files.

Bus topics consumed:
  security.scan_repo      – scan a git repo path
  security.full_scan      – full security audit (repo + deps)
  security.code_audit     – deep code audit with LLM assistance

Bus topics published:
  coordinator.result.<task_id>   – results back to coordinator
  memory.store                    – store findings in knowledge base
"""

from __future__ import annotations

import asyncio
import json
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional

from agents.base_agent import BaseAgent
from agents.message_bus import BusMessage, MessageBus
from config.logging_config import security_log as log


class SecurityAnalysisAgent(BaseAgent):
    name = "security"
    description = "Static analysis, vulnerability detection, and code auditing."

    SYSTEM_PROMPT = (
        "You are an expert defensive security analyst. "
        "Analyse the provided code or findings and identify security vulnerabilities, "
        "insecure patterns, and suggest specific remediations. "
        "Be concise, precise, and actionable."
    )

    async def _register_subscriptions(self) -> None:
        self._bus.subscribe("security.scan_repo",  self._handle_scan_repo)
        self._bus.subscribe("security.full_scan",  self._handle_full_scan)
        self._bus.subscribe("security.code_audit", self._handle_code_audit)
        self._bus.subscribe("security.test_tool",  self._handle_test_tool)

    # ------------------------------------------------------------------
    # Handlers
    # ------------------------------------------------------------------

    async def _handle_scan_repo(self, msg: BusMessage) -> None:
        repo_path = msg.payload.get("repo_path", ".")
        task_id   = msg.payload.get("task_id", "")
        log.info(f"SecurityAgent: scan_repo {repo_path}")

        findings = await self._run_static_analysis(repo_path)
        summary  = await self._llm_summarise(findings, repo_path)

        result = {
            "task_id":   task_id,
            "repo":      repo_path,
            "findings":  findings,
            "summary":   summary,
            "tool":      "security_agent.scan_repo",
        }

        await self._reply_and_store(msg, result)

    async def _handle_full_scan(self, msg: BusMessage) -> None:
        repo_path = msg.payload.get("repo_path", ".")
        task_id   = msg.payload.get("task_id", "")
        log.info(f"SecurityAgent: full_scan {repo_path}")

        static   = await self._run_static_analysis(repo_path)
        deps     = await self._run_dependency_scan(repo_path)
        secrets  = await self._run_secret_scan(repo_path)
        combined = {"static": static, "dependencies": deps, "secrets": secrets}

        summary = await self._llm_summarise(json.dumps(combined), repo_path)

        result = {
            "task_id": task_id,
            "repo":    repo_path,
            "results": combined,
            "summary": summary,
        }
        await self._reply_and_store(msg, result)

    async def _handle_code_audit(self, msg: BusMessage) -> None:
        code     = msg.payload.get("code", "")
        filename = msg.payload.get("filename", "unknown")
        task_id  = msg.payload.get("task_id", "")
        log.info(f"SecurityAgent: code_audit {filename}")

        prompt = (
            f"Perform a thorough security audit of the following code "
            f"(file: {filename}):\n\n```\n{code[:8000]}\n```\n\n"
            "List every security issue with: severity, description, line hint, fix."
        )
        analysis = await self.llm_query(prompt, system=self.SYSTEM_PROMPT)

        result = {
            "task_id":   task_id,
            "filename":  filename,
            "analysis":  analysis,
            "tool":      "security_agent.code_audit",
        }
        await self._reply_and_store(msg, result)

    # ------------------------------------------------------------------
    # Analysis helpers
    # ------------------------------------------------------------------

    async def _run_static_analysis(self, repo_path: str) -> Dict[str, Any]:
        """Run bandit (Python SAST) if available."""
        results: Dict[str, Any] = {"tool": "bandit", "issues": [], "error": None}
        try:
            proc = await asyncio.create_subprocess_exec(
                "bandit", "-r", repo_path, "-f", "json", "-q",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=60)
            try:
                data = json.loads(stdout.decode())
                results["issues"] = data.get("results", [])
                results["metrics"] = data.get("metrics", {})
            except json.JSONDecodeError:
                results["raw"] = stdout.decode()[:2000]
        except FileNotFoundError:
            results["error"] = "bandit not installed – skipped"
        except asyncio.TimeoutError:
            results["error"] = "bandit timed out"
        except Exception as exc:
            results["error"] = str(exc)
        return results

    async def _run_dependency_scan(self, repo_path: str) -> Dict[str, Any]:
        """Run safety check for known vulnerable packages."""
        results: Dict[str, Any] = {"tool": "safety", "vulnerabilities": [], "error": None}
        req_file = Path(repo_path) / "requirements.txt"
        if not req_file.exists():
            results["error"] = "requirements.txt not found"
            return results
        try:
            proc = await asyncio.create_subprocess_exec(
                "safety", "check", "-r", str(req_file), "--json",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=60)
            data = json.loads(stdout.decode())
            results["vulnerabilities"] = data
        except FileNotFoundError:
            results["error"] = "safety not installed – skipped"
        except Exception as exc:
            results["error"] = str(exc)
        return results

    async def _run_secret_scan(self, repo_path: str) -> Dict[str, Any]:
        """
        Simple regex-based secret detection.
        In production, integrate truffleHog or gitleaks here.
        """
        import re

        patterns = {
            "aws_key":        r"AKIA[0-9A-Z]{16}",
            "private_key":    r"-----BEGIN (RSA |EC )?PRIVATE KEY",
            "password_assign": r"(?i)(password|passwd|pwd)\s*=\s*['\"][^'\"]{6,}",
            "api_key":        r"(?i)api[_-]?key\s*=\s*['\"][^'\"]{16,}",
            "jwt_token":      r"eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+",
        }

        findings: List[Dict] = []
        repo = Path(repo_path)

        for py_file in list(repo.rglob("*.py"))[:200]:
            try:
                text = py_file.read_text(errors="ignore")
                for name, pat in patterns.items():
                    for match in re.finditer(pat, text):
                        findings.append({
                            "file":    str(py_file.relative_to(repo)),
                            "pattern": name,
                            "match":   match.group()[:60] + "...",
                            "line":    text[: match.start()].count("\n") + 1,
                        })
            except Exception:
                pass

        return {"tool": "regex_secret_scan", "findings": findings}

    async def _llm_summarise(self, findings: Any, context: str) -> str:
        text = findings if isinstance(findings, str) else json.dumps(findings)[:4000]
        prompt = (
            f"Context: {context}\n\n"
            f"Security scan findings:\n{text}\n\n"
            "Provide a brief executive summary (3-5 sentences) of the security "
            "posture and top 3 critical recommendations."
        )
        return await self.llm_query(prompt, system=self.SYSTEM_PROMPT)

    async def handle(self, description: str, context: str = "") -> str:
        """Direct callable for swarm use — security analysis."""
        prompt = description
        if context:
            prompt = f"{context}\n\nTask: {description}"
        return await self.llm_query(prompt, system=self.SYSTEM_PROMPT)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    async def _reply_and_store(self, msg: BusMessage, result: Dict) -> None:
        """Reply to coordinator and store findings in memory."""
        if msg.reply_to:
            await self._bus.publish(
                BusMessage(
                    topic=msg.reply_to,
                    sender=self.name,
                    recipient=msg.sender,
                    payload=result,
                )
            )
        # Persist to knowledge base
        await self._bus.publish(
            BusMessage(
                topic="memory.store",
                sender=self.name,
                payload={
                    "category": "security_analysis",
                    "text":     result.get("summary", ""),
                    "metadata": {"repo": result.get("repo", ""), "task_id": result.get("task_id", "")},
                },
            )
        )

    # ------------------------------------------------------------------
    # Dynamic tool security testing
    # ------------------------------------------------------------------

    async def _handle_test_tool(self, msg: BusMessage) -> None:
        """
        Payload: { code, tool_name, test_input (optional) }
        Tests generated tool code in a sandbox subprocess and optionally
        via bandit static analysis.
        Replies to msg.reply_to with:
          { passed, score, output, bandit_issues, tool_name }
        """
        code      = msg.payload.get("code", "")
        tool_name = msg.payload.get("tool_name", "unknown")
        test_input = msg.payload.get("test_input", {})

        log.info(f"SecurityAgent: testing generated tool '{tool_name}'")

        if not code:
            result = {"passed": False, "score": 0, "output": "No code provided",
                      "bandit_issues": [], "tool_name": tool_name}
        else:
            passed, output, bandit_issues = await self._test_tool_code(
                code, test_input
            )
            # Score 0-100: sandbox pass = 60pts, each bandit HIGH = -20pts
            score = 60 if passed else 0
            score -= sum(20 for i in bandit_issues if i.get("severity") == "HIGH")
            score -= sum(10 for i in bandit_issues if i.get("severity") == "MEDIUM")
            score = max(0, min(100, score))

            result = {
                "passed": passed and score >= 40,
                "score": score,
                "output": output,
                "bandit_issues": bandit_issues,
                "tool_name": tool_name,
            }

        if msg.reply_to:
            await self._bus.publish(BusMessage(
                topic=msg.reply_to,
                sender=self.name,
                recipient=msg.sender,
                payload=result,
            ))

    async def _test_tool_code(
        self,
        code: str,
        test_input: Dict[str, Any],
    ) -> tuple[bool, str, List[Dict]]:
        """
        Run sandbox execution + bandit static analysis on generated tool code.
        Returns (passed, output_text, bandit_issues).
        """
        from tools.sandbox_tester import sandbox_test_tool

        # 1. Sandbox execution
        passed, output = await sandbox_test_tool(code, test_input, timeout=30)

        # 2. Bandit static analysis (best-effort, non-blocking)
        bandit_issues: List[Dict] = []
        try:
            import tempfile, json as _json
            with tempfile.NamedTemporaryFile(
                mode="w", suffix=".py", delete=False, encoding="utf-8"
            ) as tf:
                tf.write(code)
                tf_path = tf.name

            proc = await asyncio.create_subprocess_exec(
                "bandit", "-f", "json", "-q", tf_path,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=20)
            Path(tf_path).unlink(missing_ok=True)
            data = _json.loads(stdout.decode(errors="replace") or "{}")
            for r in data.get("results", []):
                bandit_issues.append({
                    "severity": r.get("issue_severity", ""),
                    "confidence": r.get("issue_confidence", ""),
                    "text": r.get("issue_text", ""),
                    "line": r.get("line_number", 0),
                })
        except (FileNotFoundError, asyncio.TimeoutError):
            pass  # bandit not installed or timed out — skip
        except Exception as exc:
            log.warning(f"SecurityAgent: bandit check failed: {exc}")

        return passed, output, bandit_issues


# ---------------------------------------------------------------------------
# Backward-compatible alias so the module can be imported as SecurityAgent
# ---------------------------------------------------------------------------
SecurityAgent = SecurityAnalysisAgent
