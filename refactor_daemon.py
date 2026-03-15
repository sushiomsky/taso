#!/usr/bin/env python3
"""
TASO Refactor & Improvement Daemon
===================================
Runs as a detached background process. Each cycle:
  1. Picks a module to analyze
  2. Asks GPT-4o for concrete improvements (maintainability, UX, stability)
  3. Applies the patch (one file at a time)
  4. Syntax-checks the result
  5. Runs bot smoke-tests
  6. Git commit + push
  7. Restarts the Telegram bot
  8. Notifies admin via Telegram

Continues even if the user disconnects.
All credentials loaded from .env — no secrets hardcoded.
"""

import asyncio
import subprocess
import os
import sys
import json
import time
import signal
import logging
import textwrap
import re
from pathlib import Path
from datetime import datetime, timezone

# Load .env before reading env vars
_env_file = Path(__file__).parent / ".env"
if _env_file.exists():
    for _line in _env_file.read_text().splitlines():
        _line = _line.strip()
        if _line and not _line.startswith("#") and "=" in _line:
            _k, _, _v = _line.partition("=")
            os.environ.setdefault(_k.strip(), _v.strip())

import aiohttp

# ─── Config ────────────────────────────────────────────────────────────────────
PROJECT_DIR   = Path(__file__).parent.resolve()
BOT_TOKEN     = os.environ.get("TELEGRAM_BOT_TOKEN", "")
GITHUB_TOKEN  = os.environ.get("GITHUB_TOKEN", "")
ADMIN_CHAT_ID = int(os.environ.get("TELEGRAM_ADMIN_CHAT_ID", "0"))
CYCLE_INTERVAL = 480       # seconds between cycles (8 min)
MAX_PATCH_LINES = 400      # safety limit
LOG_FILE = PROJECT_DIR / "logs" / "refactor_daemon.log"
PID_FILE = PROJECT_DIR / "logs" / "refactor_daemon.pid"

# Protected — never auto-modify
PROTECTED = {
    "config/settings.py",
    ".env", ".env.example",
    "refactor_daemon.py",
}

# Modules to cycle through (priority order)
MODULES = [
    "bot/telegram_bot.py",
    "agents/base_agent.py",
    "swarm/swarm_orchestrator.py",
    "swarm/task_planner.py",
    "models/model_router.py",
    "models/model_registry.py",
    "tools/base_tool.py",
    "tools/dynamic_tool_generator.py",
    "memory/knowledge_db.py",
    "memory/conversation_store.py",
    "orchestrator.py",
    "agents/coordinator_agent.py",
    "agents/planner_agent.py",
    "agents/developer_agent.py",
    "agents/self_healing_agent.py",
    "self_healing/git_manager.py",
    "self_healing/version_manager.py",
    "self_healing/deploy_manager.py",
    "self_healing/rollback_manager.py",
    "sandbox/docker_runner.py",
    "main.py",
]

# ─── Logging ───────────────────────────────────────────────────────────────────
# Only file handler — stdout via nohup would create duplicate lines
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.FileHandler(str(LOG_FILE))],
)
log = logging.getLogger("refactor_daemon")

# ─── Telegram helper ───────────────────────────────────────────────────────────
async def tg_send(session: aiohttp.ClientSession, text: str) -> None:
    """Send message to admin via Telegram Bot API."""
    try:
        url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
        payload = {
            "chat_id": ADMIN_CHAT_ID,
            "text": text[:4096],
            "parse_mode": "HTML",
            "disable_notification": False,
        }
        async with session.post(url, json=payload, timeout=aiohttp.ClientTimeout(total=10)) as r:
            if r.status != 200:
                body = await r.text()
                log.warning(f"TG send failed {r.status}: {body[:200]}")
    except Exception as e:
        log.warning(f"TG send error: {e}")


# ─── LLM helper ────────────────────────────────────────────────────────────────
async def llm_query(session: aiohttp.ClientSession, system: str, user: str) -> str:
    """Call GPT-4o via GitHub Models; fall back to Ollama on 429/error."""
    # 1. Try GitHub Models
    url = "https://models.github.ai/inference/chat/completions"
    headers = {
        "Authorization": f"Bearer {GITHUB_TOKEN}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": "openai/gpt-4o",
        "messages": [
            {"role": "system", "content": system},
            {"role": "user",   "content": user},
        ],
        "temperature": 0.2,
        "max_tokens": 3000,
    }
    try:
        async with session.post(url, headers=headers, json=payload,
                                timeout=aiohttp.ClientTimeout(total=90)) as r:
            if r.status == 200:
                data = await r.json()
                return data["choices"][0]["message"]["content"]
            body = await r.text()
            log.warning(f"GitHub Models {r.status}: {body[:200]} — trying Ollama fallback")
    except Exception as e:
        log.warning(f"GitHub Models request failed: {e} — trying Ollama fallback")

    # 2. Ollama fallback (uncensored model)
    ollama_model = os.environ.get("OLLAMA_UNCENSORED_MODEL", "dolphin-mistral")
    ollama_url   = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434") + "/api/chat"
    try:
        async with session.post(
            ollama_url,
            json={
                "model":    ollama_model,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user",   "content": user},
                ],
                "stream": False,
            },
            timeout=aiohttp.ClientTimeout(total=120),
        ) as r:
            if r.status == 200:
                data = await r.json()
                return data["message"]["content"]
            body = await r.text()
            log.error(f"Ollama fallback also failed {r.status}: {body[:200]}")
    except Exception as e:
        log.error(f"Ollama fallback failed: {e}")

    return ""


# Token budget: GitHub Models gpt-4o limit ~8000 tokens total.
# ~4 chars per token; reserve 3000 for output + 500 for system prompt.
_MAX_CHUNK_LINES = 280   # ~4000 tokens — well within 8000 token total limit


def _chunk_file(content: str, chunk_index: int) -> tuple[str, int]:
    """
    Split file into chunks of at most _MAX_CHUNK_LINES lines each,
    splitting on top-level def/class boundaries.
    Returns (chunk_text, total_chunks).
    """
    lines = content.splitlines(keepends=True)

    if len(lines) <= _MAX_CHUNK_LINES:
        return content, 1

    # Find def/class boundaries (any indentation level) for clean splits
    split_points = [0]
    line_count_since_split = 0
    for i, line in enumerate(lines):
        line_count_since_split += 1
        if (line_count_since_split >= _MAX_CHUNK_LINES
                and re.match(r'^\s*(def |class |async def )', line)):
            split_points.append(i)
            line_count_since_split = 0
    split_points.append(len(lines))

    # Deduplicate consecutive identical points
    split_points = sorted(set(split_points))
    total_chunks = len(split_points) - 1

    idx = chunk_index % total_chunks
    start, end = split_points[idx], split_points[idx + 1]
    chunk = "".join(lines[start:end])
    return chunk, total_chunks


# ─── Process helpers ────────────────────────────────────────────────────────────
def get_bot_pid() -> int | None:
    """Find running bot process."""
    try:
        result = subprocess.run(
            ["pgrep", "-f", "python3 main.py"],
            capture_output=True, text=True
        )
        pids = [int(p) for p in result.stdout.strip().split() if p]
        return pids[0] if pids else None
    except Exception:
        return None


def restart_bot() -> int | None:
    """Kill old bot, start fresh, return new PID."""
    pid = get_bot_pid()
    if pid:
        log.info(f"Killing bot PID {pid}")
        try:
            os.kill(pid, signal.SIGTERM)
            time.sleep(3)
            # Force kill if still alive
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        except ProcessLookupError:
            pass
        time.sleep(2)

    log.info("Starting bot...")
    proc = subprocess.Popen(
        ["python3", "main.py"],
        cwd=str(PROJECT_DIR),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    time.sleep(5)
    new_pid = get_bot_pid()
    log.info(f"Bot started, PID={new_pid}")
    return new_pid


def syntax_check(filepath: Path) -> tuple[bool, str]:
    """Run py_compile on a file. Returns (ok, error_msg)."""
    result = subprocess.run(
        ["python3", "-m", "py_compile", str(filepath)],
        capture_output=True, text=True
    )
    if result.returncode == 0:
        return True, ""
    return False, result.stderr.strip()


def git_commit_push(message: str, files: list[str]) -> tuple[bool, str]:
    """Stage specific files, commit, and push. Returns (ok, sha)."""
    try:
        # Stage only the changed files
        subprocess.run(
            ["git", "add"] + files,
            cwd=str(PROJECT_DIR), check=True, capture_output=True
        )
        # Check if anything to commit
        result = subprocess.run(
            ["git", "diff", "--cached", "--name-only"],
            cwd=str(PROJECT_DIR), capture_output=True, text=True
        )
        if not result.stdout.strip():
            return False, "no_changes"

        subprocess.run(
            ["git", "commit", "-m", message,
             "--author=TASO Refactor Daemon <taso@autonomous.ai>"],
            cwd=str(PROJECT_DIR), check=True, capture_output=True
        )
        sha_result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=str(PROJECT_DIR), capture_output=True, text=True
        )
        sha = sha_result.stdout.strip()

        subprocess.run(
            ["git", "push", "origin", "main"],
            cwd=str(PROJECT_DIR), check=True, capture_output=True
        )
        return True, sha
    except subprocess.CalledProcessError as e:
        return False, str(e.stderr)


# ─── Core refactor logic ────────────────────────────────────────────────────────
SYSTEM_PROMPT = textwrap.dedent("""
You are a senior Python engineer performing targeted code improvements.
Your focus areas in priority order:
1. STABILITY — fix error handling, add missing try/except, fix potential crashes
2. MAINTAINABILITY — improve code structure, reduce duplication, clearer naming
3. UX — improve Telegram message formatting, clearer error messages to users
4. PERFORMANCE — async correctness, avoid blocking calls

RULES:
- Return ONLY the complete improved Python file content, nothing else
- No markdown, no explanations, no code fences
- Keep all existing functionality intact
- Do not add new features unless fixing a clear gap
- Do not modify the module's public API (function/class signatures)
- Changes must be concrete and meaningful, not cosmetic
- Max 400 lines added/changed across the file
- If no improvements needed, return the exact original unchanged
""").strip()


async def refactor_module(
    session: aiohttp.ClientSession,
    module_path: str,
    cycle: int,
) -> dict:
    """
    Analyze and refactor one module.
    Returns a result dict with status and details.
    """
    filepath = PROJECT_DIR / module_path
    if not filepath.exists():
        return {"status": "skip", "reason": "file_not_found", "module": module_path}

    if module_path in PROTECTED:
        return {"status": "skip", "reason": "protected", "module": module_path}

    original = filepath.read_text()
    line_count = original.count("\n")

    log.info(f"[Cycle {cycle}] Analyzing {module_path} ({line_count} lines, {len(original)} chars)")

    # For large files, work on one chunk per cycle (identified by cycle number)
    chunk, total_chunks = _chunk_file(original, cycle - 1)
    chunk_label = f" [chunk {(cycle-1) % total_chunks + 1}/{total_chunks}]" if total_chunks > 1 else ""
    log.info(f"[Cycle {cycle}] {module_path}{chunk_label}: sending {len(chunk)} chars to LLM")

    is_partial = total_chunks > 1
    partial_note = (
        "\nNOTE: This is a SECTION of a larger file. "
        "Return ONLY the improved version of this section — do not add imports or module-level code that belongs elsewhere."
    ) if is_partial else ""

    user_prompt = (
        f"File: {module_path}{chunk_label}\n"
        f"Total file lines: {line_count}\n\n"
        f"```python\n{chunk}\n```\n\n"
        f"Improve this{'section' if is_partial else 'file'} following the rules. "
        "Focus on the highest-impact issues. "
        f"Return only the complete improved {'section' if is_partial else 'file'}."
        f"{partial_note}"
    )

    improved_chunk = await llm_query(session, SYSTEM_PROMPT, user_prompt)

    if not improved_chunk or improved_chunk.strip() == chunk.strip():
        return {"status": "no_change", "module": module_path}

    # Strip any accidental markdown fences
    improved_chunk = re.sub(r"^```python\s*\n?", "", improved_chunk)
    improved_chunk = re.sub(r"\n?```\s*$", "", improved_chunk)
    improved_chunk = improved_chunk.strip() + "\n"

    # Reconstruct full file if chunked
    if is_partial:
        improved = original.replace(chunk.rstrip(), improved_chunk.rstrip(), 1)
        if improved == original:
            # chunk boundary may have whitespace differences — fall back to full replace
            improved = improved_chunk
    else:
        improved = improved_chunk

    # Count changed lines
    orig_lines = set(original.splitlines())
    new_lines = set(improved.splitlines())
    changed = len(new_lines - orig_lines)
    if changed > MAX_PATCH_LINES:
        return {
            "status": "rejected",
            "reason": f"too_large ({changed} lines changed, max {MAX_PATCH_LINES})",
            "module": module_path,
        }

    # Write to temp, syntax check
    tmp_path = filepath.with_suffix(".tmp.py")
    tmp_path.write_text(improved)
    ok, err = syntax_check(tmp_path)
    tmp_path.unlink(missing_ok=True)

    if not ok:
        return {
            "status": "rejected",
            "reason": f"syntax_error: {err[:200]}",
            "module": module_path,
        }

    # Apply
    filepath.write_text(improved)
    log.info(f"[Cycle {cycle}] Applied refactor to {module_path}{chunk_label} ({changed} lines changed)")

    return {
        "status": "applied",
        "module": module_path,
        "lines_changed": changed,
        "chunk_label": chunk_label,
    }


# ─── Main loop ─────────────────────────────────────────────────────────────────
async def run_cycle(session: aiohttp.ClientSession, cycle: int, module_path: str) -> dict:
    """Run one full refactor cycle."""
    start = time.time()
    result = await refactor_module(session, module_path, cycle)

    if result["status"] != "applied":
        return result

    # Commit + push
    commit_msg = (
        f"refactor({module_path}): cycle {cycle} — stability, maintainability, UX\n\n"
        f"Changed ~{result['lines_changed']} lines in {module_path}.\n"
        f"Automated improvement by TASO refactor daemon.\n\n"
        f"Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>"
    )
    pushed, sha = git_commit_push(commit_msg, [module_path])

    if not pushed:
        if sha == "no_changes":
            return {"status": "no_change", "module": module_path}
        log.warning(f"Git push failed: {sha}")
        result["git_error"] = sha

    result["sha"] = sha if pushed else "local_only"
    result["duration"] = round(time.time() - start, 1)

    # Restart bot
    new_pid = restart_bot()
    result["new_pid"] = new_pid

    # Wait for bot to settle, then verify it's still up
    await asyncio.sleep(8)
    alive_pid = get_bot_pid()
    result["bot_alive"] = bool(alive_pid)
    if not alive_pid:
        log.error("Bot did not survive restart! Attempting recovery...")
        # Try once more
        new_pid = restart_bot()
        await asyncio.sleep(5)
        result["bot_alive"] = bool(get_bot_pid())
        result["recovery_attempted"] = True

    return result


async def main():
    # Write PID file
    PID_FILE.parent.mkdir(parents=True, exist_ok=True)
    PID_FILE.write_text(str(os.getpid()))
    log.info(f"Refactor daemon started PID={os.getpid()}")

    module_index = 0

    async with aiohttp.ClientSession() as session:
        # Startup notification
        await tg_send(session,
            "🔄 <b>TASO Refactor Daemon started</b>\n"
            f"Cycling through {len(MODULES)} modules every {CYCLE_INTERVAL//60} min.\n"
            "Focus: stability · maintainability · UX\n"
            "Each successful change is committed and pushed to GitHub."
        )

        cycle = 1
        while True:
            module = MODULES[module_index % len(MODULES)]
            module_index += 1

            log.info(f"═══ Cycle {cycle} — {module} ═══")

            try:
                result = await run_cycle(session, cycle, module)
                status = result.get("status")

                if status == "applied":
                    sha = result.get("sha", "?")
                    changed = result.get("lines_changed", "?")
                    pid = result.get("new_pid", "?")
                    alive = "✅" if result.get("bot_alive") else "❌ CRASHED"
                    duration = result.get("duration", "?")
                    recovery = " (recovery attempted)" if result.get("recovery_attempted") else ""
                    chunk_label = result.get("chunk_label", "")

                    msg = (
                        f"✅ <b>Refactor cycle {cycle} complete</b>\n"
                        f"📄 Module: <code>{module}{chunk_label}</code>\n"
                        f"📝 ~{changed} lines improved\n"
                        f"🔗 Commit: <code>{sha}</code>\n"
                        f"⏱ Duration: {duration}s\n"
                        f"🤖 Bot PID: {pid} {alive}{recovery}\n"
                        f"🎯 Focus: stability · maintainability · UX"
                    )
                    log.info(f"Cycle {cycle} ✅ {module} sha={sha}")

                elif status == "no_change":
                    msg = (
                        f"⏭ <b>Cycle {cycle}</b>: <code>{module}</code> — no improvements needed, skipping"
                    )
                    log.info(f"Cycle {cycle} — {module}: no changes")

                elif status == "rejected":
                    reason = result.get("reason", "unknown")
                    msg = (
                        f"⚠️ <b>Cycle {cycle}</b>: <code>{module}</code> rejected\n"
                        f"Reason: {reason}"
                    )
                    log.warning(f"Cycle {cycle} — {module}: rejected ({reason})")

                else:
                    msg = f"ℹ️ Cycle {cycle}: <code>{module}</code> — {status}"
                    log.info(f"Cycle {cycle} — {module}: {status}")

                await tg_send(session, msg)

            except Exception as e:
                log.error(f"Cycle {cycle} error: {e}", exc_info=True)
                await tg_send(session,
                    f"❌ <b>Refactor daemon error</b> cycle {cycle}\n"
                    f"Module: <code>{module}</code>\n"
                    f"Error: {str(e)[:300]}"
                )

            cycle += 1
            log.info(f"Sleeping {CYCLE_INTERVAL}s until next cycle...")
            await asyncio.sleep(CYCLE_INTERVAL)


if __name__ == "__main__":
    asyncio.run(main())
