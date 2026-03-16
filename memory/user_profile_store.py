"""
TASO – UserProfileStore

Persists per-user profiles, interaction events, and usage statistics.
Each Telegram user gets an isolated profile that drives personalisation.

Tables
------
user_profiles   – one row per user: style, active plugins, shortcuts, metadata
user_events     – timestamped event log (intent, command, feedback, plugin_unlock)
user_stats      – aggregated intent/command counts per user (for fast threshold checks)
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import aiosqlite

from config.logging_config import get_logger
from config.settings import settings

log = get_logger("user_profile_store")

DB_PATH = str(settings.DB_PATH.parent / "user_profiles.db")

# How many messages to keep in the events table per user before trimming
MAX_EVENTS_PER_USER = 500


class UserProfile:
    """In-memory view of one user's profile."""

    def __init__(self, row: Optional[dict] = None, **kwargs: Any) -> None:
        data: Dict[str, Any] = dict(row or {})
        if kwargs:
            data.update(kwargs)

        self.user_id: int = int(data.get("user_id", 0))
        self.username: str = data.get("username") or ""
        self.first_name: str = data.get("first_name") or ""
        # "concise" | "detailed" | "technical" | "casual" — detected from usage
        self.response_style: str = data.get("response_style") or "balanced"
        # List of plugin IDs currently active for this user
        active_plugins = data.get("active_plugins") or []
        if isinstance(active_plugins, str):
            try:
                active_plugins = json.loads(active_plugins)
            except json.JSONDecodeError:
                active_plugins = []
        self.active_plugins: List[str] = list(active_plugins)
        # User-specific shortcut phrases → intent mappings
        # e.g. {"check everything": "status", "my scanner": "security_scan"}
        learned_shortcuts = data.get("learned_shortcuts") or {}
        if isinstance(learned_shortcuts, str):
            try:
                learned_shortcuts = json.loads(learned_shortcuts)
            except json.JSONDecodeError:
                learned_shortcuts = {}
        self.learned_shortcuts: Dict[str, str] = dict(learned_shortcuts)
        # Arbitrary metadata blob (power_user flag, onboarding_done, etc.)
        metadata = data.get("metadata") or {}
        if isinstance(metadata, str):
            try:
                metadata = json.loads(metadata)
            except json.JSONDecodeError:
                metadata = {}
        self.metadata: Dict[str, Any] = dict(metadata)
        self.created_at: str = data.get("created_at") or ""
        self.updated_at: str = data.get("updated_at") or ""

    # ------------------------------------------------------------------
    # Convenience helpers
    # ------------------------------------------------------------------

    def has_plugin(self, plugin_id: str) -> bool:
        return plugin_id in self.active_plugins

    def is_power_user(self) -> bool:
        return self.metadata.get("power_user", False)

    def total_interactions(self) -> int:
        return int(self.metadata.get("total_interactions", 0))

    def to_dict(self) -> Dict[str, Any]:
        return {
            "user_id": self.user_id,
            "username": self.username,
            "first_name": self.first_name,
            "response_style": self.response_style,
            "active_plugins": self.active_plugins,
            "learned_shortcuts": self.learned_shortcuts,
            "metadata": self.metadata,
        }


class UserProfileStore:
    """Async SQLite-backed store for all per-user personalisation data."""

    def __init__(self) -> None:
        self._db: Optional[aiosqlite.Connection] = None
        self._lock = asyncio.Lock()
        self._db_path: Optional[str] = None  # override for testing

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def connect(self) -> None:
        self._db = await aiosqlite.connect(self._db_path or DB_PATH)
        self._db.row_factory = aiosqlite.Row
        await self._db.execute("PRAGMA journal_mode=WAL")
        await self._create_tables()
        log.info(f"UserProfileStore connected: {DB_PATH}")

    async def close(self) -> None:
        if self._db:
            await self._db.close()
            self._db = None

    async def _create_tables(self) -> None:
        await self._db.executescript("""
            CREATE TABLE IF NOT EXISTS user_profiles (
                user_id         INTEGER PRIMARY KEY,
                username        TEXT    DEFAULT '',
                first_name      TEXT    DEFAULT '',
                response_style  TEXT    DEFAULT 'balanced',
                active_plugins  TEXT    DEFAULT '[]',
                learned_shortcuts TEXT  DEFAULT '{}',
                metadata        TEXT    DEFAULT '{}',
                created_at      TEXT    NOT NULL,
                updated_at      TEXT    NOT NULL
            );

            CREATE TABLE IF NOT EXISTS user_events (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id     INTEGER NOT NULL,
                event_type  TEXT    NOT NULL,   -- intent | command | feedback | plugin_unlock
                value       TEXT    NOT NULL,   -- e.g. 'security_scan' or 'positive'
                extra       TEXT    DEFAULT '{}',
                ts          TEXT    NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_ue_user ON user_events(user_id, ts);

            CREATE TABLE IF NOT EXISTS user_stats (
                user_id     INTEGER NOT NULL,
                key         TEXT    NOT NULL,   -- e.g. 'intent:security_scan'
                count       INTEGER DEFAULT 0,
                last_used   TEXT    NOT NULL,
                PRIMARY KEY (user_id, key)
            );
        """)
        await self._db.commit()

    # ------------------------------------------------------------------
    # Profile CRUD
    # ------------------------------------------------------------------

    async def get_or_create(self, user_id: int, username: str = "", first_name: str = "") -> UserProfile:
        """Return the profile for user_id, creating a new one if needed."""
        async with self._lock:
            row = await self._fetch_row(user_id)
            if row:
                # Update username/first_name if changed
                if row["username"] != username or row["first_name"] != first_name:
                    now = _now()
                    await self._db.execute(
                        "UPDATE user_profiles SET username=?, first_name=?, updated_at=? WHERE user_id=?",
                        (username, first_name, now, user_id),
                    )
                    await self._db.commit()
                    row = await self._fetch_row(user_id)
                return UserProfile(dict(row))

            # New user
            now = _now()
            await self._db.execute(
                """INSERT INTO user_profiles
                   (user_id, username, first_name, response_style, active_plugins,
                    learned_shortcuts, metadata, created_at, updated_at)
                   VALUES (?,?,?,'balanced','[]','{}','{}',?,?)""",
                (user_id, username, first_name, now, now),
            )
            await self._db.commit()
            row = await self._fetch_row(user_id)
            log.info(f"New user profile created: {user_id} (@{username})")
            return UserProfile(dict(row))

    async def save(self, profile: UserProfile) -> None:
        """Persist an updated UserProfile back to the database."""
        async with self._lock:
            now = _now()
            await self._db.execute(
                """UPDATE user_profiles SET
                   username=?, first_name=?, response_style=?,
                   active_plugins=?, learned_shortcuts=?, metadata=?, updated_at=?
                   WHERE user_id=?""",
                (
                    profile.username,
                    profile.first_name,
                    profile.response_style,
                    json.dumps(profile.active_plugins),
                    json.dumps(profile.learned_shortcuts),
                    json.dumps(profile.metadata),
                    now,
                    profile.user_id,
                ),
            )
            await self._db.commit()

    async def _fetch_row(self, user_id: int) -> Optional[aiosqlite.Row]:
        cur = await self._db.execute(
            "SELECT * FROM user_profiles WHERE user_id=?", (user_id,)
        )
        return await cur.fetchone()

    # ------------------------------------------------------------------
    # Event logging
    # ------------------------------------------------------------------

    async def log_event(
        self,
        user_id: int,
        event_type: str,
        value: str,
        extra: Optional[Dict] = None,
    ) -> None:
        """Append an interaction event and update the stats counter."""
        now = _now()
        async with self._lock:
            await self._db.execute(
                "INSERT INTO user_events (user_id, event_type, value, extra, ts) VALUES (?,?,?,?,?)",
                (user_id, event_type, value, json.dumps(extra or {}), now),
            )
            # Upsert stats counter
            key = f"{event_type}:{value}"
            await self._db.execute(
                """INSERT INTO user_stats (user_id, key, count, last_used) VALUES (?,?,1,?)
                   ON CONFLICT(user_id, key) DO UPDATE SET count=count+1, last_used=excluded.last_used""",
                (user_id, key, now),
            )
            await self._db.commit()
        # Trim old events in background (non-blocking)
        asyncio.create_task(self._trim_events(user_id))

    async def get_stats(self, user_id: int) -> Dict[str, int]:
        """Return {key: count} for a user, sorted by most-used first."""
        cur = await self._db.execute(
            "SELECT key, count FROM user_stats WHERE user_id=? ORDER BY count DESC",
            (user_id,),
        )
        rows = await cur.fetchall()
        return {r["key"]: r["count"] for r in rows}

    async def get_top_intents(
        self,
        user_id: int,
        n: int = 5,
        limit: Optional[int] = None,
    ) -> List[str]:
        """Return the top intents for a user (supports legacy `limit` alias)."""
        top_n = limit if limit is not None else n
        stats = await self.get_stats(user_id)
        intents = {k[len("intent:"):]: v for k, v in stats.items() if k.startswith("intent:")}
        return sorted(intents, key=lambda x: intents[x], reverse=True)[:top_n]

    async def get_recent_events(self, user_id: int, limit: int = 20) -> List[Dict]:
        cur = await self._db.execute(
            "SELECT event_type, value, extra, ts FROM user_events WHERE user_id=? ORDER BY ts DESC LIMIT ?",
            (user_id, limit),
        )
        rows = await cur.fetchall()
        return [dict(r) for r in rows]

    async def _trim_events(self, user_id: int) -> None:
        """Keep only the most recent MAX_EVENTS_PER_USER events per user."""
        try:
            async with self._lock:
                await self._db.execute(
                    """DELETE FROM user_events WHERE user_id=? AND id NOT IN (
                           SELECT id FROM user_events WHERE user_id=?
                           ORDER BY ts DESC LIMIT ?
                       )""",
                    (user_id, user_id, MAX_EVENTS_PER_USER),
                )
                await self._db.commit()
        except Exception as exc:
            log.debug(f"Event trim error (non-fatal): {exc}")

    # ------------------------------------------------------------------
    # Convenience: activate/deactivate plugins
    # ------------------------------------------------------------------

    async def activate_plugin(self, user_id: int, plugin_id: str) -> None:
        profile = await self.get_or_create(user_id)
        if plugin_id not in profile.active_plugins:
            profile.active_plugins.append(plugin_id)
            await self.save(profile)
            await self.log_event(user_id, "plugin_unlock", plugin_id)
            log.info(f"Plugin '{plugin_id}' activated for user {user_id}")

    async def deactivate_plugin(self, user_id: int, plugin_id: str) -> None:
        profile = await self.get_or_create(user_id)
        if plugin_id in profile.active_plugins:
            profile.active_plugins.remove(plugin_id)
            await self.save(profile)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# Module singleton — connected lazily by the orchestrator
user_profile_store = UserProfileStore()
