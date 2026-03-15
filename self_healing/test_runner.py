"""
TASO – Self-Healing Test Runner

Runs existing tests + basic import smoke test before any commit.
All execution is in isolated subprocesses.
"""
from __future__ import annotations
import asyncio
import sys
from pathlib import Path
from typing import Tuple

from config.logging_config import get_logger
from config.settings import settings

log = get_logger("test_runner")


async def run_smoke_test() -> Tuple[bool, str]:
    """
    Attempt to import the main modules. Returns (passed, output).
    Fast check that the codebase isn't obviously broken.
    """
    smoke = f"""
import sys
sys.path.insert(0, '{settings.GIT_REPO_PATH}')
errors = []
modules = [
    'config.settings', 'agents.message_bus', 'agents.base_agent',
    'tools.base_tool', 'memory.knowledge_db',
]
for mod in modules:
    try:
        __import__(mod)
    except Exception as e:
        errors.append(f'{{mod}}: {{e}}')
if errors:
    print('FAIL:\\n' + '\\n'.join(errors))
    sys.exit(1)
else:
    print('PASS: all modules importable')
"""
    return await _run_python(smoke, timeout=30)


async def run_pytest(test_dir: str = "tests") -> Tuple[bool, str]:
    """Run pytest if tests/ directory exists."""
    test_path = settings.GIT_REPO_PATH / test_dir
    if not test_path.exists():
        return True, "[no tests dir — skipping pytest]"

    proc = await asyncio.create_subprocess_exec(
        sys.executable, "-m", "pytest", str(test_path), "-x", "-q", "--tb=short",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=str(settings.GIT_REPO_PATH),
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=120)
    except asyncio.TimeoutError:
        proc.kill()
        return False, "[pytest timeout after 120s]"

    out = (stdout + stderr).decode(errors="replace")
    return proc.returncode == 0, out


async def run_syntax_check(files: list = None) -> Tuple[bool, str]:
    """Check Python syntax of changed files."""
    if not files:
        # Check all .py files in project
        check_code = f"""
import ast, sys
from pathlib import Path
errors = []
for f in Path('{settings.GIT_REPO_PATH}').rglob('*.py'):
    try:
        ast.parse(f.read_text())
    except SyntaxError as e:
        errors.append(f'{{f}}: {{e}}')
if errors:
    print('\\n'.join(errors))
    sys.exit(1)
else:
    print(f'Syntax OK: {{len(list(Path("{settings.GIT_REPO_PATH}").rglob("*.py")))}} files')
"""
        return await _run_python(check_code, timeout=30)

    errors = []
    import ast
    for fp in files:
        try:
            ast.parse(Path(fp).read_text())
        except SyntaxError as e:
            errors.append(f"{fp}: {e}")
    return (len(errors) == 0), ("\n".join(errors) or "Syntax OK")


async def _run_python(code: str, timeout: int = 30) -> Tuple[bool, str]:
    proc = await asyncio.create_subprocess_exec(
        sys.executable, "-c", code,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        return False, f"[timeout after {timeout}s]"
    out = (stdout + stderr).decode(errors="replace").strip()
    return proc.returncode == 0, out


class TestRunner:
    """
    Class wrapper around the module-level test functions.
    Allows agents and tests to use a consistent object interface.
    """

    async def run_smoke(self) -> Tuple[bool, str]:
        return await run_smoke_test()

    async def run_pytest(self, test_dir: str = "tests") -> Tuple[bool, str]:
        return await run_pytest(test_dir)

    async def run_syntax_check(self, files: list = None) -> Tuple[bool, str]:
        return await run_syntax_check(files)

    async def syntax_check_code(self, code: str) -> Tuple[bool, list]:
        """
        Check Python syntax of a raw code string.
        Returns (passed: bool, errors: List[str]).
        """
        import ast
        errors: list = []
        try:
            ast.parse(code)
        except SyntaxError as e:
            errors.append(f"SyntaxError at line {e.lineno}: {e.msg}")
        return len(errors) == 0, errors
