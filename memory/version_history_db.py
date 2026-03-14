"""
TASO – Version History Database

Stores all version records, tool changes, and rollback history
in SQLite for agent swarm access.
"""
from __future__ import annotations
import json
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import aiosqlite

from config.logging_config import get_logger
from config.settings import settings

log = get_logger("version_history_db")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS version_history (
    version_id      TEXT PRIMARY KEY,
    commit_sha      TEXT,
    author_agent    TEXT NOT NULL,
    change_type     TEXT NOT NULL,
    description     TEXT NOT NULL,
    files_changed   TEXT,        -- JSON array
    test_passed     INTEGER DEFAULT 0,
    deployed        INTEGER DEFAULT 0,
    stable          INTEGER DEFAULT 0,
    timestamp       REAL NOT NULL,
    metadata        TEXT         -- JSON object
);

CREATE TABLE IF NOT EXISTS tool_history (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    tool_name       TEXT NOT NULL,
    version         TEXT NOT NULL,
    action          TEXT NOT NULL,  -- created|updated|deleted|tested
    agent           TEXT NOT NULL,
    test_passed     INTEGER DEFAULT 0,
    test_output     TEXT,
    code_hash       TEXT,
    timestamp       REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS rollback_log (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    reason          TEXT NOT NULL,
    from_sha        TEXT,
    to_sha          TEXT,
    success         INTEGER DEFAULT 0,
    triggered_by    TEXT,
    timestamp       REAL NOT NULL
);
"""


class VersionHistoryDB:
    def __init__(self, path: Path = None) -> None:
        self._path = path or (settings.BASE_DIR / "data" / "version_history.db")
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._db: Optional[aiosqlite.Connection] = None

    async def connect(self) -> None:
        self._db = await aiosqlite.connect(str(self._path))
        await self._db.executescript(_SCHEMA)
        await self._db.commit()
        log.info(f"VersionHistoryDB connected: {self._path}")

    async def close(self) -> None:
        if self._db:
            await self._db.close()

    async def log_version(self, rec) -> None:
        """Accept a VersionRecord from version_manager."""
        await self._db.execute(
            """INSERT OR REPLACE INTO version_history
               (version_id, commit_sha, author_agent, change_type, description,
                files_changed, test_passed, deployed, stable, timestamp, metadata)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (
                rec.version_id, rec.commit_sha, rec.author_agent, rec.change_type,
                rec.description, json.dumps(rec.files_changed),
                int(rec.test_passed), int(rec.deployed), int(rec.stable),
                rec.timestamp, json.dumps(rec.metadata),
            ),
        )
        await self._db.commit()

    async def log_tool(self, tool_name: str, version: str, action: str,
                        agent: str, test_passed: bool = False,
                        test_output: str = "", code_hash: str = "") -> None:
        await self._db.execute(
            """INSERT INTO tool_history
               (tool_name, version, action, agent, test_passed, test_output, code_hash, timestamp)
               VALUES (?,?,?,?,?,?,?,?)""",
            (tool_name, version, action, agent,
             int(test_passed), test_output, code_hash, time.time()),
        )
        await self._db.commit()

    async def log_rollback(self, reason: str, from_sha: str, to_sha: str,
                            success: bool, triggered_by: str = "auto") -> None:
        await self._db.execute(
            """INSERT INTO rollback_log (reason, from_sha, to_sha, success, triggered_by, timestamp)
               VALUES (?,?,?,?,?,?)""",
            (reason, from_sha, to_sha, int(success), triggered_by, time.time()),
        )
        await self._db.commit()

    async def recent_versions(self, limit: int = 10) -> List[Dict]:
        if not self._db:
            return []
        async with self._db.execute(
            "SELECT version_id, commit_sha, author_agent, change_type, description, "
            "test_passed, stable, timestamp FROM version_history ORDER BY timestamp DESC LIMIT ?",
            (limit,),
        ) as cursor:
            rows = await cursor.fetchall()
        return [
            {"version_id": r[0], "sha": r[1], "agent": r[2], "type": r[3],
             "desc": r[4], "tested": bool(r[5]), "stable": bool(r[6]), "ts": r[7]}
            for r in rows
        ]

    async def recent_tools(self, limit: int = 20) -> List[Dict]:
        if not self._db:
            return []
        async with self._db.execute(
            "SELECT tool_name, version, action, agent, test_passed, timestamp "
            "FROM tool_history ORDER BY timestamp DESC LIMIT ?",
            (limit,),
        ) as cursor:
            rows = await cursor.fetchall()
        return [
            {"tool": r[0], "version": r[1], "action": r[2],
             "agent": r[3], "passed": bool(r[4]), "ts": r[5]}
            for r in rows
        ]

    async def recent_rollbacks(self, limit: int = 10) -> List[Dict]:
        if not self._db:
            return []
        async with self._db.execute(
            "SELECT reason, from_sha, to_sha, success, triggered_by, timestamp "
            "FROM rollback_log ORDER BY timestamp DESC LIMIT ?",
            (limit,),
        ) as cursor:
            rows = await cursor.fetchall()
        return [
            {"reason": r[0], "from": r[1], "to": r[2],
             "success": bool(r[3]), "by": r[4], "ts": r[5]}
            for r in rows
        ]


version_history_db = VersionHistoryDB()
