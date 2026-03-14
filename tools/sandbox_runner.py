"""
TASO – Tool: sandbox_runner

Runs arbitrary code snippets inside isolated Docker containers.
Enforces resource limits, captures stdout/stderr, and auto-cleans up.
"""

from __future__ import annotations

import asyncio
import tempfile
import uuid
from pathlib import Path
from typing import Any, Dict, Optional

from config.settings import settings
from config.logging_config import tool_log as log
from tools.base_tool import BaseTool, ToolSchema


class SandboxRunnerTool(BaseTool):
    name        = "sandbox_runner"
    description = "Execute code in an isolated Docker sandbox with resource limits."
    schema      = ToolSchema({
        "code": {
            "type": "str", "required": True,
            "description": "Python code to execute.",
        },
        "language": {
            "type": "str", "required": False, "default": "python",
            "description": "Language: python (default).",
        },
        "timeout": {
            "type": "int", "required": False,
            "description": "Execution timeout in seconds (default from settings).",
        },
    })

    async def execute(self, code: str, language: str = "python",
                       timeout: Optional[int] = None, **_: Any) -> Dict[str, Any]:
        if language != "python":
            raise ValueError(f"Language '{language}' not yet supported.")

        timeout = timeout or settings.DOCKER_TIMEOUT

        # Write code to a temp file
        with tempfile.NamedTemporaryFile(
            suffix=".py", mode="w", delete=False, prefix="taso_sandbox_"
        ) as fh:
            fh.write(code)
            code_file = Path(fh.name)

        container_name = f"taso_sandbox_{uuid.uuid4().hex[:8]}"
        network_mode   = "bridge" if settings.DOCKER_NETWORK_ENABLED else "none"

        try:
            return await self._run_in_docker(
                code_file, container_name, timeout, network_mode
            )
        finally:
            code_file.unlink(missing_ok=True)
            await self._cleanup_container(container_name)

    # ------------------------------------------------------------------

    async def _run_in_docker(
        self, code_file: Path, container_name: str,
        timeout: int, network_mode: str = "none"
    ) -> Dict[str, Any]:
        cmd = [
            "docker", "run",
            "--rm",
            "--name", container_name,
            "--network", network_mode,
            "--memory", settings.DOCKER_MEM_LIMIT,
            "--cpu-quota", str(settings.DOCKER_CPU_QUOTA),
            "--security-opt", "no-new-privileges",
            "--cap-drop", "ALL",
            "-v", f"{code_file}:/sandbox/script.py:ro",
            settings.DOCKER_SANDBOX_IMAGE,
            "python", "/sandbox/script.py",
        ]

        # Only add read-only + tmpfs when network is isolated
        if network_mode == "none":
            cmd = (
                cmd[:3]
                + ["--read-only", "--tmpfs", "/tmp:rw,noexec,nosuid,size=64m"]
                + cmd[3:]
            )

        log.info(f"Sandbox: starting container {container_name}")

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=float(timeout) + 5
            )
        except asyncio.TimeoutError:
            await self._cleanup_container(container_name)
            return {
                "exit_code": -1,
                "stdout":    "",
                "stderr":    "",
                "timed_out": True,
                "error":     f"Execution timed out after {timeout}s",
            }
        except FileNotFoundError:
            return {
                "exit_code": -1,
                "stdout":    "",
                "stderr":    "",
                "timed_out": False,
                "error":     "Docker not installed or not in PATH.",
            }

        return {
            "exit_code": proc.returncode,
            "stdout":    stdout.decode(errors="replace")[:10_000],
            "stderr":    stderr.decode(errors="replace")[:2_000],
            "timed_out": False,
            "error":     None,
        }

    @staticmethod
    async def _cleanup_container(name: str) -> None:
        try:
            proc = await asyncio.create_subprocess_exec(
                "docker", "rm", "-f", name,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await asyncio.wait_for(proc.wait(), timeout=10)
        except Exception:
            pass
