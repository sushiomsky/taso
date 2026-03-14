"""
TASO – Knowledge database (SQLite).

Provides structured, persistent storage for:
  • threat intelligence (CVEs, advisories)
  • analysis results
  • remediation patterns
  • audit log entries
  • tool execution records

Uses aiosqlite for async access.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import aiosqlite

from config.settings import settings
from config.logging_config import get_logger

log = get_logger("agent")

_DB_PATH: Path = settings.DB_PATH

# ---------------------------------------------------------------------------
# DDL
# ---------------------------------------------------------------------------

_DDL = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS cves (
    cve_id       TEXT PRIMARY KEY,
    description  TEXT,
    severity     TEXT,
    cvss_score   REAL,
    published    TEXT,
    modified     TEXT,
    source       TEXT,
    raw_json     TEXT,
    created_at   TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);

CREATE TABLE IF NOT EXISTS advisories (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    title        TEXT NOT NULL,
    source       TEXT,
    url          TEXT,
    severity     TEXT,
    summary      TEXT,
    raw_content  TEXT,
    created_at   TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);

CREATE TABLE IF NOT EXISTS analysis_results (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    target       TEXT NOT NULL,
    agent        TEXT NOT NULL,
    result_type  TEXT NOT NULL,
    summary      TEXT,
    detail_json  TEXT,
    severity     TEXT,
    created_at   TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);

CREATE TABLE IF NOT EXISTS remediation_patterns (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    pattern_type TEXT NOT NULL,
    language     TEXT,
    description  TEXT,
    before_code  TEXT,
    after_code   TEXT,
    confidence   REAL DEFAULT 0.0,
    usage_count  INTEGER DEFAULT 0,
    created_at   TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);

CREATE TABLE IF NOT EXISTS audit_log (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    actor        TEXT NOT NULL,
    action       TEXT NOT NULL,
    target       TEXT,
    status       TEXT,
    detail_json  TEXT,
    ts           TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);

CREATE TABLE IF NOT EXISTS tool_executions (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    tool_name    TEXT NOT NULL,
    invoked_by   TEXT,
    input_json   TEXT,
    output_json  TEXT,
    success      INTEGER DEFAULT 0,
    duration_ms  INTEGER,
    ts           TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);

CREATE INDEX IF NOT EXISTS idx_cves_severity   ON cves(severity);
CREATE INDEX IF NOT EXISTS idx_audit_actor     ON audit_log(actor);
CREATE INDEX IF NOT EXISTS idx_audit_action    ON audit_log(action);
CREATE INDEX IF NOT EXISTS idx_tool_name       ON tool_executions(tool_name);
CREATE INDEX IF NOT EXISTS idx_analysis_target ON analysis_results(target);
"""


# ---------------------------------------------------------------------------
# Database wrapper
# ---------------------------------------------------------------------------

class KnowledgeDB:
    """Async SQLite knowledge database."""

    def __init__(self, path: Path = _DB_PATH) -> None:
        self._path = path
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._conn: Optional[aiosqlite.Connection] = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def connect(self) -> None:
        self._conn = await aiosqlite.connect(str(self._path))
        self._conn.row_factory = aiosqlite.Row
        await self._conn.executescript(_DDL)
        await self._conn.commit()
        log.info(f"KnowledgeDB connected: {self._path}")

    async def close(self) -> None:
        if self._conn:
            await self._conn.close()
            self._conn = None

    async def __aenter__(self) -> "KnowledgeDB":
        await self.connect()
        return self

    async def __aexit__(self, *_: Any) -> None:
        await self.close()

    # ------------------------------------------------------------------
    # CVEs
    # ------------------------------------------------------------------

    async def upsert_cve(self, cve_id: str, description: str, severity: str,
                         cvss_score: float, published: str, modified: str,
                         source: str, raw: Dict) -> None:
        await self._conn.execute(
            """INSERT INTO cves (cve_id, description, severity, cvss_score,
               published, modified, source, raw_json)
               VALUES (?,?,?,?,?,?,?,?)
               ON CONFLICT(cve_id) DO UPDATE SET
               description=excluded.description,
               severity=excluded.severity,
               cvss_score=excluded.cvss_score,
               modified=excluded.modified,
               raw_json=excluded.raw_json""",
            (cve_id, description, severity, cvss_score, published,
             modified, source, json.dumps(raw)),
        )
        await self._conn.commit()

    async def get_cves(self, severity: Optional[str] = None,
                       limit: int = 50) -> List[Dict]:
        if severity:
            cur = await self._conn.execute(
                "SELECT * FROM cves WHERE severity=? ORDER BY published DESC LIMIT ?",
                (severity, limit),
            )
        else:
            cur = await self._conn.execute(
                "SELECT * FROM cves ORDER BY published DESC LIMIT ?", (limit,)
            )
        rows = await cur.fetchall()
        return [dict(r) for r in rows]

    async def search_cves(self, query: str, limit: int = 20) -> List[Dict]:
        cur = await self._conn.execute(
            "SELECT * FROM cves WHERE description LIKE ? OR cve_id LIKE ? LIMIT ?",
            (f"%{query}%", f"%{query}%", limit),
        )
        rows = await cur.fetchall()
        return [dict(r) for r in rows]

    # ------------------------------------------------------------------
    # Advisories
    # ------------------------------------------------------------------

    async def insert_advisory(self, title: str, source: str, url: str,
                               severity: str, summary: str, raw: str) -> int:
        cur = await self._conn.execute(
            """INSERT INTO advisories (title, source, url, severity, summary, raw_content)
               VALUES (?,?,?,?,?,?)""",
            (title, source, url, severity, summary, raw),
        )
        await self._conn.commit()
        return cur.lastrowid

    async def get_advisories(self, limit: int = 50) -> List[Dict]:
        cur = await self._conn.execute(
            "SELECT * FROM advisories ORDER BY created_at DESC LIMIT ?", (limit,)
        )
        return [dict(r) for r in await cur.fetchall()]

    # ------------------------------------------------------------------
    # Analysis results
    # ------------------------------------------------------------------

    async def insert_analysis(self, target: str, agent: str,
                               result_type: str, summary: str,
                               detail: Dict, severity: str = "info") -> int:
        cur = await self._conn.execute(
            """INSERT INTO analysis_results
               (target, agent, result_type, summary, detail_json, severity)
               VALUES (?,?,?,?,?,?)""",
            (target, agent, result_type, summary, json.dumps(detail), severity),
        )
        await self._conn.commit()
        return cur.lastrowid

    async def get_analyses(self, target: Optional[str] = None,
                            limit: int = 50) -> List[Dict]:
        if target:
            cur = await self._conn.execute(
                "SELECT * FROM analysis_results WHERE target=? ORDER BY created_at DESC LIMIT ?",
                (target, limit),
            )
        else:
            cur = await self._conn.execute(
                "SELECT * FROM analysis_results ORDER BY created_at DESC LIMIT ?",
                (limit,),
            )
        return [dict(r) for r in await cur.fetchall()]

    # ------------------------------------------------------------------
    # Remediation patterns
    # ------------------------------------------------------------------

    async def insert_pattern(self, pattern_type: str, language: str,
                              description: str, before: str,
                              after: str, confidence: float) -> int:
        cur = await self._conn.execute(
            """INSERT INTO remediation_patterns
               (pattern_type, language, description, before_code, after_code, confidence)
               VALUES (?,?,?,?,?,?)""",
            (pattern_type, language, description, before, after, confidence),
        )
        await self._conn.commit()
        return cur.lastrowid

    async def get_patterns(self, language: Optional[str] = None,
                            limit: int = 50) -> List[Dict]:
        if language:
            cur = await self._conn.execute(
                "SELECT * FROM remediation_patterns WHERE language=? ORDER BY confidence DESC LIMIT ?",
                (language, limit),
            )
        else:
            cur = await self._conn.execute(
                "SELECT * FROM remediation_patterns ORDER BY confidence DESC LIMIT ?",
                (limit,),
            )
        return [dict(r) for r in await cur.fetchall()]

    async def increment_pattern_usage(self, pattern_id: int) -> None:
        await self._conn.execute(
            "UPDATE remediation_patterns SET usage_count=usage_count+1 WHERE id=?",
            (pattern_id,),
        )
        await self._conn.commit()

    # ------------------------------------------------------------------
    # Audit log
    # ------------------------------------------------------------------

    async def audit(self, actor: str, action: str, target: str = "",
                    status: str = "ok", detail: Optional[Dict] = None) -> None:
        await self._conn.execute(
            "INSERT INTO audit_log (actor, action, target, status, detail_json) VALUES (?,?,?,?,?)",
            (actor, action, target, status, json.dumps(detail or {})),
        )
        await self._conn.commit()

    async def get_audit_log(self, actor: Optional[str] = None,
                             action: Optional[str] = None,
                             limit: int = 100) -> List[Dict]:
        clauses, params = [], []
        if actor:
            clauses.append("actor=?"); params.append(actor)
        if action:
            clauses.append("action=?"); params.append(action)
        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        params.append(limit)
        cur = await self._conn.execute(
            f"SELECT * FROM audit_log {where} ORDER BY ts DESC LIMIT ?",
            params,
        )
        return [dict(r) for r in await cur.fetchall()]

    # ------------------------------------------------------------------
    # Tool executions
    # ------------------------------------------------------------------

    async def log_tool_execution(self, tool_name: str, invoked_by: str,
                                  inputs: Dict, outputs: Dict,
                                  success: bool, duration_ms: int) -> None:
        await self._conn.execute(
            """INSERT INTO tool_executions
               (tool_name, invoked_by, input_json, output_json, success, duration_ms)
               VALUES (?,?,?,?,?,?)""",
            (tool_name, invoked_by, json.dumps(inputs), json.dumps(outputs),
             int(success), duration_ms),
        )
        await self._conn.commit()

    async def get_tool_stats(self) -> List[Dict]:
        cur = await self._conn.execute(
            """SELECT tool_name,
                      COUNT(*) as total,
                      SUM(success) as successes,
                      AVG(duration_ms) as avg_ms
               FROM tool_executions
               GROUP BY tool_name
               ORDER BY total DESC"""
        )
        return [dict(r) for r in await cur.fetchall()]

    # ------------------------------------------------------------------
    # Stats
    # ------------------------------------------------------------------

    async def stats(self) -> Dict[str, int]:
        tables = ["cves", "advisories", "analysis_results",
                  "remediation_patterns", "audit_log", "tool_executions"]
        result = {}
        for t in tables:
            cur = await self._conn.execute(f"SELECT COUNT(*) FROM {t}")
            row = await cur.fetchone()
            result[t] = row[0]
        return result
