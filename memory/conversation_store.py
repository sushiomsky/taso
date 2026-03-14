"""
TASO – Conversation store.

Persists Telegram conversation history per chat_id so the AI can
maintain context across sessions. Backed by the same SQLite database
used by KnowledgeDB (different tables).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

import aiosqlite

from config.settings import settings
from config.logging_config import get_logger

log = get_logger("agent")

_DDL = """
PRAGMA journal_mode=WAL;

CREATE TABLE IF NOT EXISTS conversations (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id    INTEGER NOT NULL,
    role       TEXT NOT NULL,      -- 'user' | 'assistant' | 'system'
    content    TEXT NOT NULL,
    ts         TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);

CREATE INDEX IF NOT EXISTS idx_conv_chat ON conversations(chat_id, ts);

CREATE TABLE IF NOT EXISTS conversation_summaries (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id    INTEGER NOT NULL UNIQUE,
    summary    TEXT,
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);
"""


class ConversationStore:
    """Async conversation history store backed by SQLite."""

    # Each chat gets at most this many raw messages before we summarise
    MAX_MESSAGES = 50

    def __init__(self, path: Optional[Path] = None) -> None:
        self._path = path or (settings.DB_PATH.parent / "conversations.db")
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._conn: Optional[aiosqlite.Connection] = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def connect(self) -> None:
        try:
            self._conn = await aiosqlite.connect(str(self._path))
            self._conn.row_factory = aiosqlite.Row
            await self._conn.executescript(_DDL)
            await self._conn.commit()
            log.info(f"ConversationStore connected: {self._path}")
        except Exception as e:
            log.error(f"Failed to connect to ConversationStore: {e}")
            raise

    async def close(self) -> None:
        if self._conn:
            try:
                await self._conn.close()
                self._conn = None
            except Exception as e:
                log.error(f"Failed to close ConversationStore connection: {e}")
                raise

    async def __aenter__(self) -> "ConversationStore":
        await self.connect()
        return self

    async def __aexit__(self, *_: Any) -> None:
        await self.close()

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------

    async def add_message(self, chat_id: int, role: str, content: str) -> None:
        """Append one turn to the conversation."""
        try:
            await self._conn.execute(
                "INSERT INTO conversations (chat_id, role, content) VALUES (?,?,?)",
                (chat_id, role, content),
            )
            await self._conn.commit()
            # Trim old messages beyond MAX_MESSAGES
            await self._trim(chat_id)
        except Exception as e:
            log.error(f"Failed to add message to ConversationStore: {e}")
            raise

    async def get_history(self, chat_id: int, limit: int = 20) -> List[Dict[str, str]]:
        """Return the most recent *limit* messages as dicts."""
        try:
            cur = await self._conn.execute(
                """SELECT role, content, ts FROM conversations
                   WHERE chat_id=? ORDER BY ts DESC LIMIT ?""",
                (chat_id, limit),
            )
            rows = await cur.fetchall()
            # Reverse so oldest comes first
            return [{"role": r["role"], "content": r["content"]} for r in reversed(rows)]
        except Exception as e:
            log.error(f"Failed to retrieve conversation history: {e}")
            raise

    async def get_context(self, chat_id: int) -> List[Dict[str, str]]:
        """
        Return conversation context suitable for passing to an LLM.
        Includes the stored summary (if any) as a system message, followed
        by the most recent raw messages.
        """
        try:
            messages: List[Dict[str, str]] = []

            summary = await self.get_summary(chat_id)
            if summary:
                messages.append(
                    {"role": "system", "content": f"Previous conversation summary: {summary}"}
                )

            messages.extend(await self.get_history(chat_id, limit=10))
            return messages
        except Exception as e:
            log.error(f"Failed to retrieve conversation context: {e}")
            raise

    async def clear(self, chat_id: int) -> None:
        """Delete all messages for a chat."""
        try:
            await self._conn.execute(
                "DELETE FROM conversations WHERE chat_id=?", (chat_id,)
            )
            await self._conn.execute(
                "DELETE FROM conversation_summaries WHERE chat_id=?", (chat_id,)
            )
            await self._conn.commit()
        except Exception as e:
            log.error(f"Failed to clear conversation for chat_id {chat_id}: {e}")
            raise

    # ------------------------------------------------------------------
    # Summaries
    # ------------------------------------------------------------------

    async def save_summary(self, chat_id: int, summary: str) -> None:
        try:
            await self._conn.execute(
                """INSERT INTO conversation_summaries (chat_id, summary)
                   VALUES (?,?)
                   ON CONFLICT(chat_id) DO UPDATE SET
                   summary=excluded.summary,
                   updated_at=strftime('%Y-%m-%dT%H:%M:%SZ','now')""",
                (chat_id, summary),
            )
            await self._conn.commit()
        except Exception as e:
            log.error(f"Failed to save summary for chat_id {chat_id}: {e}")
            raise

    async def get_summary(self, chat_id: int) -> Optional[str]:
        try:
            cur = await self._conn.execute(
                "SELECT summary FROM conversation_summaries WHERE chat_id=?",
                (chat_id,),
            )
            row = await cur.fetchone()
            return row["summary"] if row else None
        except Exception as e:
            log.error(f"Failed to retrieve summary for chat_id {chat_id}: {e}")
            raise

    # ------------------------------------------------------------------
    # Stats
    # ------------------------------------------------------------------

    async def chat_stats(self, chat_id: int) -> Dict[str, Any]:
        try:
            cur = await self._conn.execute(
                "SELECT COUNT(*) as total FROM conversations WHERE chat_id=?",
                (chat_id,),
            )
            row = await cur.fetchone()
            return {"total_messages": row["total"], "chat_id": chat_id}
        except Exception as e:
            log.error(f"Failed to retrieve chat stats for chat_id {chat_id}: {e}")
            raise

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _trim(self, chat_id: int) -> None:
        """Keep only the most recent MAX_MESSAGES rows per chat."""
        try:
            await self._conn.execute(
                """DELETE FROM conversations WHERE chat_id=? AND id NOT IN (
                   SELECT id FROM conversations WHERE chat_id=?
                   ORDER BY ts DESC LIMIT ?)""",
                (chat_id, chat_id, self.MAX_MESSAGES),
            )
            await self._conn.commit()
        except Exception as e:
            log.error(f"Failed to trim conversation for chat_id {chat_id}: {e}")
            raise
