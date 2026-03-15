"""
TASO Crawler — Crawler Manager

Central coordinator for all crawler subsystems:
  - OnionCrawler  (Tor .onion sites)
  - ClearnetCrawler (security/hacking clearnet sites)
  - IRCIndexer    (IRC channel lurking + indexing)
  - NewsgroupIndexer (Usenet NNTP)

Provides unified start/stop/status interface used by the Telegram bot.
"""
from __future__ import annotations

import asyncio
from typing import Optional, Dict, Any

from config.logging_config import get_logger
from crawler.crawler_db import crawler_db, CrawlerDB

log = get_logger("crawler_manager")


class CrawlerManager:
    """Singleton that owns all crawler instances and their lifecycle."""

    def __init__(self) -> None:
        self._db:        CrawlerDB = crawler_db
        self._onion:     Optional[Any] = None
        self._clearnet:  Optional[Any] = None
        self._irc:       Optional[Any] = None
        self._newsgroup: Optional[Any] = None
        self._connected  = False

    # ------------------------------------------------------------------ #
    # Lifecycle
    # ------------------------------------------------------------------ #

    async def connect(self) -> None:
        """Connect the DB. Must be called once at startup."""
        if not self._connected:
            await self._db.connect()
            self._connected = True
            log.info("CrawlerManager DB connected")

    async def start_all(self) -> None:
        """Start all crawlers."""
        await self.connect()
        await self.start_onion()
        await self.start_clearnet()
        await self.start_irc()
        await self.start_newsgroup()

    async def stop_all(self) -> None:
        """Stop all crawlers gracefully."""
        for name, crawler in [
            ("onion",     self._onion),
            ("clearnet",  self._clearnet),
            ("irc",       self._irc),
            ("newsgroup", self._newsgroup),
        ]:
            if crawler:
                try:
                    await crawler.stop()
                    log.info(f"{name} crawler stopped")
                except Exception as exc:
                    log.warning(f"Error stopping {name}: {exc}")

    # ------------------------------------------------------------------ #
    # Per-crawler start/stop
    # ------------------------------------------------------------------ #

    async def start_onion(self) -> str:
        await self.connect()
        from crawler.onion_crawler import OnionCrawler
        if self._onion and self._onion.is_running:
            return "Onion crawler already running."
        self._onion = OnionCrawler(self._db)
        await self._onion.start()
        return "🧅 Onion crawler started."

    async def stop_onion(self) -> str:
        if self._onion:
            await self._onion.stop()
            return "🛑 Onion crawler stopped."
        return "Onion crawler was not running."

    async def start_clearnet(self) -> str:
        await self.connect()
        from crawler.clearnet_crawler import ClearnetCrawler
        if self._clearnet and self._clearnet.is_running:
            return "Clearnet crawler already running."
        self._clearnet = ClearnetCrawler(self._db)
        await self._clearnet.start()
        return "🌐 Clearnet crawler started."

    async def stop_clearnet(self) -> str:
        if self._clearnet:
            await self._clearnet.stop()
            return "🛑 Clearnet crawler stopped."
        return "Clearnet crawler was not running."

    async def start_irc(self) -> str:
        await self.connect()
        from crawler.irc_indexer import IRCIndexer
        if self._irc and self._irc.is_running:
            return "IRC indexer already running."
        self._irc = IRCIndexer(self._db)
        await self._irc.start()
        return "💬 IRC indexer started."

    async def stop_irc(self) -> str:
        if self._irc:
            await self._irc.stop()
            return "🛑 IRC indexer stopped."
        return "IRC indexer was not running."

    async def start_newsgroup(self) -> str:
        await self.connect()
        from crawler.newsgroup_indexer import NewsgroupIndexer
        if self._newsgroup and self._newsgroup.is_running:
            return "Newsgroup indexer already running."
        self._newsgroup = NewsgroupIndexer(self._db)
        await self._newsgroup.start()
        return "📰 Newsgroup indexer started."

    async def stop_newsgroup(self) -> str:
        if self._newsgroup:
            await self._newsgroup.stop()
            return "🛑 Newsgroup indexer stopped."
        return "Newsgroup indexer was not running."

    # ------------------------------------------------------------------ #
    # URL management
    # ------------------------------------------------------------------ #

    async def add_url(self, url: str, priority: int = 8) -> str:
        """Add a URL to the appropriate crawler queue."""
        await self.connect()
        from urllib.parse import urlparse
        parsed = urlparse(url)

        if not parsed.scheme:
            url = "http://" + url
            parsed = urlparse(url)

        if not parsed.netloc:
            return "❌ Invalid URL."

        if ".onion" in parsed.netloc:
            # Register and enqueue for onion crawler
            await self._db.register_onion(parsed.netloc, tags=["manual"])
            is_new = await self._db.enqueue(url, "onion", priority=priority, depth=0)
        else:
            # Clearnet — add domain to allow-list and enqueue
            if self._clearnet:
                ok = await self._clearnet.add_url(url, priority=priority)
                is_new = ok
            else:
                is_new = await self._db.enqueue(url, "manual", priority=priority, depth=0)

        return (
            f"✅ Added to crawl queue: `{url}`"
            if is_new
            else f"ℹ️ Already in queue: `{url}`"
        )

    # ------------------------------------------------------------------ #
    # Status and search
    # ------------------------------------------------------------------ #

    async def status(self) -> Dict[str, Any]:
        await self.connect()
        db_stats = await self._db.global_stats()
        return {
            "crawlers": {
                "onion":     self._onion.is_running     if self._onion     else False,
                "clearnet":  self._clearnet.is_running  if self._clearnet  else False,
                "irc":       self._irc.is_running       if self._irc       else False,
                "newsgroup": self._newsgroup.is_running if self._newsgroup else False,
            },
            "db": db_stats,
        }

    async def search(self, query: str, limit: int = 10) -> list:
        await self.connect()
        return await self._db.search(query, limit=limit)

    async def get_onions(self, status: str = None, limit: int = 50) -> list:
        await self.connect()
        return await self._db.get_onions(status=status, limit=limit)

    async def newsgroup_fetch_now(self) -> str:
        await self.connect()
        if not self._newsgroup:
            from crawler.newsgroup_indexer import NewsgroupIndexer
            self._newsgroup = NewsgroupIndexer(self._db)
        count = await self._newsgroup.fetch_now()
        return f"📰 Fetched {count} new newsgroup articles."

    def format_status(self, st: Dict) -> str:
        """Format the status dict into a Telegram-friendly markdown string."""
        crawlers = st["crawlers"]
        db       = st["db"]
        q        = db.get("queue", {})

        def icon(running): return "🟢" if running else "🔴"

        lines = [
            "🕷️ *Crawler Status*\n",
            f"{icon(crawlers['onion'])} Onion crawler",
            f"{icon(crawlers['clearnet'])} Clearnet crawler",
            f"{icon(crawlers['irc'])} IRC indexer",
            f"{icon(crawlers['newsgroup'])} Newsgroup indexer",
            "",
            "📊 *Database*",
            f"• Queue — pending: `{q.get('pending', 0)}` | done: `{q.get('done', 0)}` | failed: `{q.get('failed', 0)}`",
            f"• Pages indexed: `{db.get('pages_total', 0)}`",
            f"• Onion addresses: `{db.get('onion_total', 0)}` ({db.get('onion_alive', 0)} alive)",
            f"• IRC messages: `{db.get('irc_messages', 0)}`",
            f"• Newsgroup posts: `{db.get('ng_posts', 0)}`",
        ]
        return "\n".join(lines)


# Module singleton
crawler_manager = CrawlerManager()
