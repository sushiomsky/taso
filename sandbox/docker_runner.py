"""
TASO – Docker runner (sandbox infrastructure helper).

This module provides low-level Docker container management used by the
SandboxRunnerTool and the self-improvement test runner.

Features:
  • run_code()          – execute a code snippet in a fresh container
  • run_command()       – run an arbitrary command in a container
  • pull_image()        – ensure the sandbox image is present
  • ContainerResult     – structured result dataclass
"""

from __future__ import annotations

import asyncio
import tempfile
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from config.settings import settings
from config.logging_config import tool_log as log


@dataclass
class ContainerResult:
    exit_code: int
    stdout: str
    stderr: str
    timed_out: bool = False
    container_id: str = ""
    error: Optional[str] = None

    @property
    def success(self) -> bool:
        return self.exit_code == 0 and not self.timed_out and not self.error


def _default_network_mode(force_network: Optional[bool] = None) -> str:
    """
    Determine Docker network mode:
      - If force_network is explicitly passed, honour it.
      - Otherwise, use the DOCKER_NETWORK_ENABLED setting.
    """
    enabled = force_network if force_network is not None else settings.DOCKER_NETWORK_ENABLED
    return "bridge" if enabled else "none"


async def run_code(
    code: str,
    image: str = "",
    timeout: int = 0,
    env: Dict[str, str] = {},
    packages: List[str] = [],
    network: Optional[bool] = None,
) -> ContainerResult:
    """
    Execute Python *code* in an isolated Docker container.

    network=None  → use DOCKER_NETWORK_ENABLED setting
    network=True  → bridge (outbound access)
    network=False → none   (fully isolated)

    Packages are pip-installed before the script runs when provided.
    """
    image = image or settings.DOCKER_SANDBOX_IMAGE
    timeout = timeout or settings.DOCKER_TIMEOUT

    # If packages requested and network disabled, enable network automatically
    if packages and network is None and not settings.DOCKER_NETWORK_ENABLED:
        log.warning("sandbox: packages requested – temporarily enabling network for pip install")
        network = True

    # Write code to temp file
    try:
        with tempfile.NamedTemporaryFile(
            suffix=".py", mode="w", delete=False, prefix="taso_"
        ) as fh:
            fh.write(code)
            code_file = Path(fh.name)

        container_name = f"taso_{uuid.uuid4().hex[:8]}"

        return await _docker_run(
            image=image,
            name=container_name,
            code_file=code_file,
            timeout=timeout,
            env=env,
            packages=packages,
            network_mode=_default_network_mode(network),
        )
    except Exception as exc:
        log.error(f"Error in run_code: {exc}")
        return ContainerResult(
            exit_code=-1, stdout="", stderr="", error=str(exc)
        )
    finally:
        try:
            if 'code_file' in locals():
                code_file.unlink(missing_ok=True)
        except Exception as exc:
            log.warning(f"Failed to delete temp file: {exc}")
        await _force_remove(container_name)


async def run_command(
    command: List[str],
    image: str = "",
    workdir: str = "/workspace",
    timeout: int = 0,
    env: Dict[str, str] = {},
    mounts: Dict[str, str] = {},  # {host_path: container_path}
    network: Optional[bool] = None,
) -> ContainerResult:
    """Run an arbitrary command in a throwaway container."""
    image = image or settings.DOCKER_SANDBOX_IMAGE
    timeout = timeout or settings.DOCKER_TIMEOUT
    name = f"taso_{uuid.uuid4().hex[:8]}"

    cmd = [
        "docker", "run", "--rm",
        "--name", name,
        "--network", _default_network_mode(network),
        "--memory", settings.DOCKER_MEM_LIMIT,
        "--cpu-quota", str(settings.DOCKER_CPU_QUOTA),
        "--security-opt", "no-new-privileges",
        "--cap-drop", "ALL",
        "--workdir", workdir,
    ]

    for k, v in env.items():
        cmd += ["-e", f"{k}={v}"]

    for host, cont in mounts.items():
        cmd += ["-v", f"{host}:{cont}:ro"]

    cmd += [image] + command

    return await _run_docker_cmd(cmd, name, timeout)


async def pull_image(image: str = "") -> bool:
    """Pull the sandbox image if not already present."""
    image = image or settings.DOCKER_SANDBOX_IMAGE
    try:
        proc = await asyncio.create_subprocess_exec(
            "docker", "pull", image,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await asyncio.wait_for(proc.communicate(), timeout=300)
        if proc.returncode == 0:
            log.info(f"Docker image ready: {image}")
            return True
        else:
            log.warning(f"docker pull failed: {stderr.decode(errors='replace')[:200]}")
            return False
    except asyncio.TimeoutError:
        log.error(f"Docker pull timed out for image: {image}")
        return False
    except Exception as exc:
        log.error(f"pull_image error: {exc}")
        return False


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

async def _docker_run(
    image: str, name: str, code_file: Path,
    timeout: int, env: Dict[str, str],
    packages: List[str], network_mode: str,
) -> ContainerResult:
    # Build startup script: optionally install packages first
    startup = ""
    if packages:
        pkgs = " ".join(packages)
        startup = f"pip install -q {pkgs} && "

    cmd = [
        "docker", "run", "--rm",
        "--name", name,
        f"--network={network_mode}",
        "--memory", settings.DOCKER_MEM_LIMIT,
        "--cpu-quota", str(settings.DOCKER_CPU_QUOTA),
        "--security-opt", "no-new-privileges",
        "--cap-drop", "ALL",
        "--tmpfs", "/tmp:rw,noexec,nosuid,size=64m",
        "-v", f"{code_file}:/sandbox/script.py:ro",
    ]

    for k, v in env.items():
        cmd += ["-e", f"{k}={v}"]

    if startup:
        cmd += [image, "sh", "-c", f"{startup}python /sandbox/script.py"]
    else:
        cmd += [image, "python", "/sandbox/script.py"]

    return await _run_docker_cmd(cmd, name, timeout)


async def _run_docker_cmd(cmd: List[str], name: str, timeout: int) -> ContainerResult:
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(), timeout=float(timeout) + 5
        )
        return ContainerResult(
            exit_code=proc.returncode,
            stdout=stdout.decode(errors="replace")[:20_000],
            stderr=stderr.decode(errors="replace")[:2_000],
            container_id=name,
        )
    except asyncio.TimeoutError:
        await _force_remove(name)
        return ContainerResult(
            exit_code=-1, stdout="", stderr="",
            timed_out=True,
            error=f"Timed out after {timeout}s",
            container_id=name,
        )
    except FileNotFoundError:
        return ContainerResult(
            exit_code=-1, stdout="", stderr="",
            error="Docker not found in PATH",
        )
    except Exception as exc:
        log.error(f"Unexpected error in _run_docker_cmd: {exc}")
        return ContainerResult(
            exit_code=-1, stdout="", stderr="",
            error=str(exc),
        )


async def _force_remove(name: str) -> None:
    try:
        proc = await asyncio.create_subprocess_exec(
            "docker", "rm", "-f", name,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await asyncio.wait_for(proc.wait(), timeout=10)
    except asyncio.TimeoutError:
        log.warning(f"Timeout while force-removing container: {name}")
    except Exception as exc:
        log.warning(f"Error during force-remove of container {name}: {exc}")
