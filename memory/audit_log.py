"""
TASO – memory/audit_log.py

Unified, append-only audit log backed by SQLite.

Every significant action in the system (agent tasks, tool execution,
self-improvement patches, sandbox runs, rollbacks) should be recorded
here so operators can reconstruct the full history.

Usage::

    from memory.audit_log import audit_log

    await audit_log.record(
        agent="security_agent",
        action="code_audit",
        input_summary="main.py – 300 lines",
        output_summary="3 HIGH issues found",
        success=True,
        metadata={"repo": "/root/taso", "issues": 3},
    )

    entries = await audit_log.query(agent="security_agent", limit=20)
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

import aiosqlite

from config.logging_config import get_logger
from config.settings import settings

log = get_logger("audit")

# Module-level project root (same as config/settings.py BASE_DIR)
_BASE_DIR = Path(__file__).resolve().parent.parent


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class AuditEntry:
    id:             int
    ts:             float
    agent:          str
    action:         str
    input_hash:     str
    output_hash:    str
    input_summary:  str
    output_summary: str
    success:        bool
    error:          Optional[str]
    metadata:       Dict[str, Any]

    @property
    def dt(self) -> str:
        """Human-readable UTC timestamp."""
        import datetime
        return datetime.datetime.fromtimestamp(self.ts, tz=datetime.timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


# ---------------------------------------------------------------------------
# AuditLog
# ---------------------------------------------------------------------------

class AuditLog:
    """
    Thread-safe, async-friendly audit log.

    All writes are serialised through an asyncio.Lock to prevent
    concurrent writes to the SQLite file.
    """

    def __init__(self, path: Optional[Path] = None) -> None:
        if path is None:
            data_dir = _BASE_DIR / "data"
            data_dir.mkdir(parents=True, exist_ok=True)
            path = data_dir / "audit_log.db"
        self._path  = path
        self._lock  = asyncio.Lock()
        self._ready = False

    # ------------------------------------------------------------------
    # Initialisation
    # ------------------------------------------------------------------

    async def connect(self) -> None:
        """Create the database schema if it does not exist."""
        async with self._lock:
            async with aiosqlite.connect(str(self._path)) as db:
                await db.execute("""
                    CREATE TABLE IF NOT EXISTS audit_log (
                        id             INTEGER PRIMARY KEY AUTOINCREMENT,
                        ts             REAL    NOT NULL,
                        agent          TEXT    NOT NULL,
                        action         TEXT    NOT NULL,
                        input_hash     TEXT    NOT NULL DEFAULT '',
                        output_hash    TEXT    NOT NULL DEFAULT '',
                        input_summary  TEXT    NOT NULL DEFAULT '',
                        output_summary TEXT    NOT NULL DEFAULT '',
                        success        INTEGER NOT NULL DEFAULT 1,
                        error          TEXT,
                        metadata       TEXT    NOT NULL DEFAULT '{}'
                    )
                """)
                await db.execute(
                    "CREATE INDEX IF NOT EXISTS idx_agent  ON audit_log (agent)"
                )
                await db.execute(
                    "CREATE INDEX IF NOT EXISTS idx_action ON audit_log (action)"
                )
                await db.execute(
                    "CREATE INDEX IF NOT EXISTS idx_ts     ON audit_log (ts)"
                )
                await db.commit()
        self._ready = True
        log.debug(f"AuditLog connected: {self._path}")

    async def _ensure_ready(self) -> None:
        if not self._ready:
            await self.connect()

    # ------------------------------------------------------------------
    # Write
    # ------------------------------------------------------------------

    async def record(
        self,
        agent:          str,
        action:         str,
        input_summary:  str  = "",
        output_summary: str  = "",
        success:        bool = True,
        error:          Optional[str] = None,
        metadata:       Optional[Dict[str, Any]] = None,
    ) -> int:
        """
        Append one audit entry.  Returns the new row id.
        input_summary / output_summary are stored verbatim (truncated to 1 KB).
        SHA-256 hashes are computed and stored alongside.
        """
        await self._ensure_ready()

        def _hash(s: str) -> str:
            return hashlib.sha256(s.encode()).hexdigest()[:16] if s else ""

        row = (
            time.time(),
            agent,
            action,
            _hash(input_summary),
            _hash(output_summary),
            input_summary[:1024],
            output_summary[:1024],
            int(success),
            error[:512] if error else None,
            json.dumps(metadata or {}),
        )

        async with self._lock:
            async with aiosqlite.connect(str(self._path)) as db:
                cur = await db.execute(
                    """
                    INSERT INTO audit_log
                        (ts, agent, action,
                         input_hash, output_hash,
                         input_summary, output_summary,
                         success, error, metadata)
                    VALUES (?,?,?,?,?,?,?,?,?,?)
                    """,
                    row,
                )
                await db.commit()
                row_id = cur.lastrowid or 0
                log.debug(f"AuditLog [{row_id}] {agent}/{action} success={success}")
                return row_id

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    async def query(
        self,
        agent:   Optional[str] = None,
        action:  Optional[str] = None,
        success: Optional[bool] = None,
        limit:   int = 50,
        offset:  int = 0,
    ) -> List[AuditEntry]:
        """
        Query audit entries.  All filter args are optional.
        Returns newest-first.
        """
        await self._ensure_ready()

        conditions: List[str] = []
        params:     List[Any] = []

        if agent is not None:
            conditions.append("agent = ?")
            params.append(agent)
        if action is not None:
            conditions.append("action = ?")
            params.append(action)
        if success is not None:
            conditions.append("success = ?")
            params.append(int(success))

        where = "WHERE " + " AND ".join(conditions) if conditions else ""
        sql   = f"SELECT * FROM audit_log {where} ORDER BY ts DESC LIMIT ? OFFSET ?"
        params += [limit, offset]

        async with aiosqlite.connect(str(self._path)) as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute(sql, params)
            rows = await cur.fetchall()

        return [
            AuditEntry(
                id=r["id"],
                ts=r["ts"],
                agent=r["agent"],
                action=r["action"],
                input_hash=r["input_hash"],
                output_hash=r["output_hash"],
                input_summary=r["input_summary"],
                output_summary=r["output_summary"],
                success=bool(r["success"]),
                error=r["error"],
                metadata=json.loads(r["metadata"] or "{}"),
            )
            for r in rows
        ]

    async def recent(self, n: int = 20) -> List[AuditEntry]:
        """Return the N most-recent entries across all agents."""
        return await self.query(limit=n)

    async def stats(self) -> Dict[str, Any]:
        """Return aggregate stats (total entries, failures per agent)."""
        await self._ensure_ready()
        async with aiosqlite.connect(str(self._path)) as db:
            cur = await db.execute("SELECT COUNT(*) FROM audit_log")
            total = (await cur.fetchone())[0]

            cur = await db.execute(
                "SELECT agent, COUNT(*) FROM audit_log WHERE success=0 GROUP BY agent"
            )
            failures = {r[0]: r[1] for r in await cur.fetchall()}

        return {"total_entries": total, "failures_by_agent": failures}

    async def format_recent(self, n: int = 10) -> str:
        """Return a human-readable summary of the N most-recent entries."""
        entries = await self.recent(n)
        if not entries:
            return "No audit entries yet."
        lines = ["📋 Recent audit entries:"]
        for e in entries:
            status = "✅" if e.success else "❌"
            lines.append(f"{status} [{e.dt}] {e.agent}/{e.action}  {e.output_summary[:80]}")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------

audit_log = AuditLog()
