"""
TASO Crawler — Database Layer

SQLite with FTS5 for full-text search across all indexed content.
Tables:
  url_queue         — pending + visited URLs (all source types)
  crawled_pages     — text content of crawled web pages
  onion_addresses   — every .onion address ever seen
  irc_messages      — IRC channel log
  newsgroup_posts   — Usenet/NNTP article store
  crawl_stats       — per-session statistics
"""
from __future__ import annotations

import asyncio
import hashlib
import time
from pathlib import Path
from typing import List, Optional, Dict, Any
from urllib.parse import urlparse

import aiosqlite

from config.logging_config import get_logger
from config.settings import settings

log = get_logger("crawler_db")

DB_PATH = str(settings.DB_PATH.parent / "crawler.db")

# URL status values
STATUS_PENDING   = "pending"
STATUS_CRAWLING  = "crawling"
STATUS_DONE      = "done"
STATUS_FAILED    = "failed"
STATUS_SKIPPED   = "skipped"

# Source type values
SRC_ONION      = "onion"
SRC_CLEARNET   = "clearnet"
SRC_IRC        = "irc"
SRC_NEWSGROUP  = "newsgroup"
SRC_MANUAL     = "manual"


def _url_id(url: str) -> str:
    """Stable 16-char ID for a URL."""
    return hashlib.sha256(url.encode()).hexdigest()[:16]


class CrawlerDB:
    """Async SQLite backend for the crawler subsystem."""

    def __init__(self, db_path: str = DB_PATH) -> None:
        self._path = db_path
        self._db: Optional[aiosqlite.Connection] = None
        self._lock = asyncio.Lock()

    # ------------------------------------------------------------------ #
    # Lifecycle
    # ------------------------------------------------------------------ #

    async def connect(self) -> None:
        self._db = await aiosqlite.connect(self._path)
        self._db.row_factory = aiosqlite.Row
        await self._db.execute("PRAGMA journal_mode=WAL")
        await self._db.execute("PRAGMA synchronous=NORMAL")
        await self._create_tables()
        log.info(f"CrawlerDB connected: {self._path}")

    async def close(self) -> None:
        if self._db:
            await self._db.close()
            self._db = None

    async def _create_tables(self) -> None:
        await self._db.executescript("""
        -- URL work queue
        CREATE TABLE IF NOT EXISTS url_queue (
            id          TEXT PRIMARY KEY,
            url         TEXT UNIQUE NOT NULL,
            source_type TEXT NOT NULL DEFAULT 'clearnet',
            status      TEXT NOT NULL DEFAULT 'pending',
            priority    INTEGER NOT NULL DEFAULT 5,
            depth       INTEGER NOT NULL DEFAULT 0,
            added_at    REAL NOT NULL,
            updated_at  REAL NOT NULL,
            retries     INTEGER NOT NULL DEFAULT 0,
            referrer    TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_queue_status  ON url_queue(status, priority DESC);
        CREATE INDEX IF NOT EXISTS idx_queue_src     ON url_queue(source_type);

        -- Full text content store
        CREATE TABLE IF NOT EXISTS crawled_pages (
            id          TEXT PRIMARY KEY,
            url         TEXT UNIQUE NOT NULL,
            title       TEXT,
            text_body   TEXT,
            source_type TEXT NOT NULL DEFAULT 'clearnet',
            http_status INTEGER,
            crawled_at  REAL NOT NULL,
            content_len INTEGER,
            lang        TEXT
        );
        -- FTS5 virtual table over crawled pages
        CREATE VIRTUAL TABLE IF NOT EXISTS pages_fts USING fts5(
            url, title, text_body,
            content='crawled_pages', content_rowid='rowid'
        );
        CREATE TRIGGER IF NOT EXISTS pages_ai AFTER INSERT ON crawled_pages BEGIN
            INSERT INTO pages_fts(rowid, url, title, text_body)
            VALUES (new.rowid, new.url, new.title, new.text_body);
        END;
        CREATE TRIGGER IF NOT EXISTS pages_au AFTER UPDATE ON crawled_pages BEGIN
            INSERT INTO pages_fts(pages_fts, rowid, url, title, text_body)
            VALUES ('delete', old.rowid, old.url, old.title, old.text_body);
            INSERT INTO pages_fts(rowid, url, title, text_body)
            VALUES (new.rowid, new.url, new.title, new.text_body);
        END;

        -- Dedicated onion address registry
        CREATE TABLE IF NOT EXISTS onion_addresses (
            address     TEXT PRIMARY KEY,
            first_seen  REAL NOT NULL,
            last_seen   REAL,
            times_seen  INTEGER NOT NULL DEFAULT 1,
            title       TEXT,
            status      TEXT DEFAULT 'unknown',
            tags        TEXT DEFAULT '[]'
        );
        CREATE INDEX IF NOT EXISTS idx_onion_status ON onion_addresses(status);

        -- IRC message log
        CREATE TABLE IF NOT EXISTS irc_messages (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            network     TEXT NOT NULL,
            channel     TEXT NOT NULL,
            nick        TEXT,
            message     TEXT NOT NULL,
            ts          REAL NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_irc_chan ON irc_messages(network, channel);
        CREATE VIRTUAL TABLE IF NOT EXISTS irc_fts USING fts5(
            network, channel, nick, message,
            content='irc_messages', content_rowid='id'
        );
        CREATE TRIGGER IF NOT EXISTS irc_ai AFTER INSERT ON irc_messages BEGIN
            INSERT INTO irc_fts(rowid, network, channel, nick, message)
            VALUES (new.id, new.network, new.channel, new.nick, new.message);
        END;

        -- Newsgroup / Usenet posts
        CREATE TABLE IF NOT EXISTS newsgroup_posts (
            message_id  TEXT PRIMARY KEY,
            newsgroup   TEXT NOT NULL,
            subject     TEXT,
            author      TEXT,
            body        TEXT,
            posted_at   REAL
        );
        CREATE INDEX IF NOT EXISTS idx_ng_group ON newsgroup_posts(newsgroup);
        CREATE VIRTUAL TABLE IF NOT EXISTS ng_fts USING fts5(
            newsgroup, subject, author, body,
            content='newsgroup_posts', content_rowid='rowid'
        );
        CREATE TRIGGER IF NOT EXISTS ng_ai AFTER INSERT ON newsgroup_posts BEGIN
            INSERT INTO ng_fts(rowid, newsgroup, subject, author, body)
            VALUES (new.rowid, new.newsgroup, new.subject, new.author, new.body);
        END;

        -- Crawl run statistics
        CREATE TABLE IF NOT EXISTS crawl_stats (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id      TEXT NOT NULL,
            source_type TEXT NOT NULL,
            pages_crawled INTEGER DEFAULT 0,
            pages_failed  INTEGER DEFAULT 0,
            onions_found  INTEGER DEFAULT 0,
            started_at  REAL NOT NULL,
            ended_at    REAL
        );
        """)
        await self._db.commit()

    # ------------------------------------------------------------------ #
    # URL Queue
    # ------------------------------------------------------------------ #

    async def enqueue(
        self,
        url: str,
        source_type: str = SRC_CLEARNET,
        priority: int = 5,
        depth: int = 0,
        referrer: str = "",
    ) -> bool:
        """Add a URL to the queue. Returns True if newly added."""
        uid = _url_id(url)
        now = time.time()
        async with self._lock:
            try:
                await self._db.execute(
                    """INSERT INTO url_queue
                       (id, url, source_type, status, priority, depth, added_at, updated_at, referrer)
                       VALUES (?,?,?,?,?,?,?,?,?)""",
                    (uid, url, source_type, STATUS_PENDING, priority, depth, now, now, referrer),
                )
                await self._db.commit()
                return True
            except aiosqlite.IntegrityError:
                return False  # already queued

    async def dequeue_batch(
        self, source_type: str, batch: int = 10
    ) -> List[Dict[str, Any]]:
        """Claim a batch of pending URLs for crawling."""
        now = time.time()
        async with self._lock:
            rows = await (
                await self._db.execute(
                    """SELECT id, url, depth, referrer FROM url_queue
                       WHERE status = ? AND source_type = ?
                       ORDER BY priority DESC, added_at ASC
                       LIMIT ?""",
                    (STATUS_PENDING, source_type, batch),
                )
            ).fetchall()
            if not rows:
                return []
            ids = [r["id"] for r in rows]
            placeholders = ",".join("?" * len(ids))
            await self._db.execute(
                f"UPDATE url_queue SET status=?, updated_at=? WHERE id IN ({placeholders})",
                [STATUS_CRAWLING, now] + ids,
            )
            await self._db.commit()
        return [dict(r) for r in rows]

    async def mark_done(self, url: str, success: bool = True) -> None:
        status = STATUS_DONE if success else STATUS_FAILED
        async with self._lock:
            await self._db.execute(
                "UPDATE url_queue SET status=?, updated_at=? WHERE url=?",
                (status, time.time(), url),
            )
            await self._db.commit()

    async def queue_stats(self) -> Dict[str, int]:
        rows = await (
            await self._db.execute(
                "SELECT status, COUNT(*) as n FROM url_queue GROUP BY status"
            )
        ).fetchall()
        return {r["status"]: r["n"] for r in rows}

    async def url_is_known(self, url: str) -> bool:
        row = await (
            await self._db.execute(
                "SELECT 1 FROM url_queue WHERE url=? LIMIT 1", (url,)
            )
        ).fetchone()
        return row is not None

    # ------------------------------------------------------------------ #
    # Content storage
    # ------------------------------------------------------------------ #

    async def save_page(
        self,
        url: str,
        title: str,
        text_body: str,
        source_type: str,
        http_status: int = 200,
        lang: str = "",
    ) -> None:
        uid = _url_id(url)
        now = time.time()
        async with self._lock:
            await self._db.execute(
                """INSERT INTO crawled_pages
                   (id, url, title, text_body, source_type, http_status, crawled_at, content_len, lang)
                   VALUES (?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(url) DO UPDATE SET
                     title=excluded.title, text_body=excluded.text_body,
                     http_status=excluded.http_status, crawled_at=excluded.crawled_at,
                     content_len=excluded.content_len""",
                (uid, url, title[:512], text_body, source_type,
                 http_status, now, len(text_body), lang),
            )
            await self._db.commit()

    # ------------------------------------------------------------------ #
    # Onion address registry
    # ------------------------------------------------------------------ #

    async def register_onion(
        self, address: str, title: str = "", tags: List[str] = None
    ) -> bool:
        """Register a .onion address. Returns True if it's new."""
        import json
        now = time.time()
        tags_json = json.dumps(tags or [])
        async with self._lock:
            existing = await (
                await self._db.execute(
                    "SELECT times_seen FROM onion_addresses WHERE address=?", (address,)
                )
            ).fetchone()
            if existing:
                await self._db.execute(
                    "UPDATE onion_addresses SET last_seen=?, times_seen=times_seen+1 WHERE address=?",
                    (now, address),
                )
                await self._db.commit()
                return False
            await self._db.execute(
                """INSERT INTO onion_addresses
                   (address, first_seen, last_seen, times_seen, title, tags)
                   VALUES (?,?,?,1,?,?)""",
                (address, now, now, title[:256] if title else "", tags_json),
            )
            await self._db.commit()
        return True

    async def get_onions(
        self, status: str = None, limit: int = 100, offset: int = 0
    ) -> List[Dict]:
        q = "SELECT * FROM onion_addresses"
        params: List = []
        if status:
            q += " WHERE status=?"
            params.append(status)
        q += " ORDER BY times_seen DESC, first_seen DESC LIMIT ? OFFSET ?"
        params += [limit, offset]
        rows = await (await self._db.execute(q, params)).fetchall()
        return [dict(r) for r in rows]

    async def count_onions(self) -> int:
        row = await (
            await self._db.execute("SELECT COUNT(*) FROM onion_addresses")
        ).fetchone()
        return row[0]

    async def update_onion_status(self, address: str, status: str, title: str = None) -> None:
        async with self._lock:
            if title:
                await self._db.execute(
                    "UPDATE onion_addresses SET status=?, title=?, last_seen=? WHERE address=?",
                    (status, title[:256], time.time(), address),
                )
            else:
                await self._db.execute(
                    "UPDATE onion_addresses SET status=?, last_seen=? WHERE address=?",
                    (status, time.time(), address),
                )
            await self._db.commit()

    # ------------------------------------------------------------------ #
    # IRC
    # ------------------------------------------------------------------ #

    async def save_irc_message(
        self,
        network: str,
        channel: str,
        nick: str,
        message: str,
    ) -> None:
        async with self._lock:
            await self._db.execute(
                "INSERT INTO irc_messages (network, channel, nick, message, ts) VALUES (?,?,?,?,?)",
                (network, channel, nick, message, time.time()),
            )
            await self._db.commit()

    async def get_irc_messages(
        self, network: str = None, channel: str = None, limit: int = 100
    ) -> List[Dict]:
        q = "SELECT * FROM irc_messages"
        params: List = []
        if network and channel:
            q += " WHERE network=? AND channel=?"
            params = [network, channel]
        elif network:
            q += " WHERE network=?"
            params = [network]
        q += " ORDER BY ts DESC LIMIT ?"
        params.append(limit)
        rows = await (await self._db.execute(q, params)).fetchall()
        return [dict(r) for r in rows]

    # ------------------------------------------------------------------ #
    # Newsgroups
    # ------------------------------------------------------------------ #

    async def save_newsgroup_post(
        self,
        message_id: str,
        newsgroup: str,
        subject: str,
        author: str,
        body: str,
        posted_at: float = None,
    ) -> bool:
        """Returns True if new (not duplicate)."""
        async with self._lock:
            try:
                await self._db.execute(
                    """INSERT INTO newsgroup_posts
                       (message_id, newsgroup, subject, author, body, posted_at)
                       VALUES (?,?,?,?,?,?)""",
                    (message_id, newsgroup, subject[:512], author[:256],
                     body, posted_at or time.time()),
                )
                await self._db.commit()
                return True
            except aiosqlite.IntegrityError:
                return False

    # ------------------------------------------------------------------ #
    # Full-text search
    # ------------------------------------------------------------------ #

    async def search(
        self, query: str, limit: int = 20, source_types: List[str] = None
    ) -> List[Dict]:
        """Full-text search across pages + IRC + newsgroups."""
        results: List[Dict] = []

        # Pages
        if not source_types or any(s in source_types for s in (SRC_ONION, SRC_CLEARNET, SRC_MANUAL)):
            rows = await (
                await self._db.execute(
                    """SELECT p.url, p.title, p.source_type, p.crawled_at,
                              snippet(pages_fts, 2, '[', ']', '...', 15) AS snippet
                       FROM pages_fts f
                       JOIN crawled_pages p ON p.rowid = f.rowid
                       WHERE pages_fts MATCH ?
                       ORDER BY rank LIMIT ?""",
                    (query, limit),
                )
            ).fetchall()
            for r in rows:
                results.append({"type": "page", **dict(r)})

        # IRC
        if not source_types or SRC_IRC in source_types:
            rows = await (
                await self._db.execute(
                    """SELECT m.network, m.channel, m.nick, m.ts,
                              snippet(irc_fts, 3, '[', ']', '...', 15) AS snippet
                       FROM irc_fts f
                       JOIN irc_messages m ON m.id = f.rowid
                       WHERE irc_fts MATCH ?
                       ORDER BY rank LIMIT ?""",
                    (query, limit),
                )
            ).fetchall()
            for r in rows:
                results.append({"type": "irc", **dict(r)})

        # Newsgroups
        if not source_types or SRC_NEWSGROUP in source_types:
            rows = await (
                await self._db.execute(
                    """SELECT p.newsgroup, p.subject, p.author, p.posted_at,
                              snippet(ng_fts, 3, '[', ']', '...', 15) AS snippet
                       FROM ng_fts f
                       JOIN newsgroup_posts p ON p.rowid = f.rowid
                       WHERE ng_fts MATCH ?
                       ORDER BY rank LIMIT ?""",
                    (query, limit),
                )
            ).fetchall()
            for r in rows:
                results.append({"type": "newsgroup", **dict(r)})

        return results[:limit]

    async def global_stats(self) -> Dict[str, Any]:
        """Return aggregate counts for all tables."""
        async def count(table: str, where: str = "") -> int:
            q = f"SELECT COUNT(*) FROM {table}"
            if where:
                q += f" WHERE {where}"
            row = await (await self._db.execute(q)).fetchone()
            return row[0]

        queue = await self.queue_stats()
        return {
            "queue":          queue,
            "pages_total":    await count("crawled_pages"),
            "onion_total":    await count("onion_addresses"),
            "onion_alive":    await count("onion_addresses", "status='alive'"),
            "irc_messages":   await count("irc_messages"),
            "ng_posts":       await count("newsgroup_posts"),
        }


# Module singleton — created fresh; caller must call connect()
crawler_db = CrawlerDB()
