"""
TASO Crawler — Onion Crawler

Crawls .onion sites via Tor SOCKS5 proxy using aiohttp + aiohttp-socks.
Implements BFS with per-domain rate limiting, onion address discovery,
and text extraction.

Requirements:
  Tor daemon running on 127.0.0.1:9050 (SOCKS5)
"""
from __future__ import annotations

import asyncio
import re
import time
from typing import Optional, Set
from urllib.parse import urlparse

from config.logging_config import get_logger
from crawler.crawler_db import CrawlerDB, SRC_ONION, STATUS_DONE
from crawler.text_extractor import extract, extract_onions_from_text
from crawler.seed_urls import ONION_SEEDS

try:
    import aiohttp
    from aiohttp_socks import ProxyConnector
    _AIOHTTP = True
except ImportError:
    _AIOHTTP = False

log = get_logger("onion_crawler")

TOR_PROXY       = "socks5://127.0.0.1:9050"
REQUEST_TIMEOUT = 45        # seconds — onion sites are slow
MAX_CONTENT     = 2_000_000 # 2 MB max response
RATE_LIMIT_SEC  = 3.0       # seconds between requests to same domain
MAX_DEPTH       = 4         # BFS depth limit
BATCH_SIZE      = 5         # URLs fetched concurrently
MAX_WORKERS     = 3         # parallel fetch workers


class OnionCrawler:
    """
    Async BFS crawler for .onion sites via Tor SOCKS5.

    Workflow:
      1. Seed the URL queue with known .onion addresses
      2. Workers dequeue URLs in batches, fetch via Tor
      3. Extract text + links + new .onion addresses
      4. Register new onions in DB and enqueue for crawling
      5. Store page text in crawled_pages
    """

    def __init__(self, db: CrawlerDB) -> None:
        self._db          = db
        self._running     = False
        self._tasks: Set[asyncio.Task] = set()
        # Per-domain last-request timestamps for rate limiting
        self._domain_ts: dict[str, float] = {}
        self._lock = asyncio.Lock()

    # ------------------------------------------------------------------ #
    # Public interface
    # ------------------------------------------------------------------ #

    async def start(self) -> None:
        """Seed DB and launch crawler workers."""
        if not _AIOHTTP:
            log.error("aiohttp / aiohttp-socks not installed — onion crawler disabled")
            return

        log.info("OnionCrawler starting…")
        self._running = True

        # Seed initial onion addresses
        for url, src, pri in ONION_SEEDS:
            if src == SRC_ONION:
                await self._db.enqueue(url, SRC_ONION, priority=pri, depth=0)
                # Also register the address
                parsed = urlparse(url)
                if parsed.netloc:
                    await self._db.register_onion(parsed.netloc, tags=["seed"])

        # Start worker tasks
        for i in range(MAX_WORKERS):
            t = asyncio.create_task(self._worker(i))
            self._tasks.add(t)
            t.add_done_callback(self._tasks.discard)

        log.info(f"OnionCrawler: {MAX_WORKERS} workers started")

    async def stop(self) -> None:
        self._running = False
        for t in list(self._tasks):
            t.cancel()
        log.info("OnionCrawler stopped")

    @property
    def is_running(self) -> bool:
        return self._running

    # ------------------------------------------------------------------ #
    # Worker loop
    # ------------------------------------------------------------------ #

    async def _worker(self, worker_id: int) -> None:
        log.debug(f"Onion worker {worker_id} started")
        connector = None
        session   = None

        try:
            connector = ProxyConnector.from_url(TOR_PROXY)
            timeout   = aiohttp.ClientTimeout(total=REQUEST_TIMEOUT, connect=20)
            session   = aiohttp.ClientSession(connector=connector, timeout=timeout)

            while self._running:
                batch = await self._db.dequeue_batch(SRC_ONION, BATCH_SIZE)
                if not batch:
                    await asyncio.sleep(10)
                    continue

                for item in batch:
                    if not self._running:
                        break
                    await self._crawl_one(session, item["url"], item["depth"])

        except asyncio.CancelledError:
            pass
        except Exception as exc:
            log.exception(f"Onion worker {worker_id} fatal error: {exc}")
        finally:
            if session:
                await session.close()
            if connector:
                await connector.close()

    async def _crawl_one(
        self, session: "aiohttp.ClientSession", url: str, depth: int
    ) -> None:
        """Fetch one URL, extract content, enqueue new links."""
        domain = urlparse(url).netloc

        # Rate limiting
        await self._rate_limit(domain)

        try:
            log.debug(f"[onion] fetching {url}")
            async with session.get(
                url,
                headers={"User-Agent": "TASO/1.0 Security Research Crawler"},
                allow_redirects=True,
                max_redirects=3,
            ) as resp:
                if resp.status == 200:
                    ct = resp.headers.get("Content-Type", "")
                    if "text" not in ct and "html" not in ct:
                        await self._db.mark_done(url, success=True)
                        return

                    raw = await resp.read()
                    if len(raw) > MAX_CONTENT:
                        raw = raw[:MAX_CONTENT]

                    title, text, links, onions = extract(
                        raw, base_url=url, encoding="utf-8"
                    )

                    # Store page content
                    await self._db.save_page(
                        url=url, title=title, text_body=text,
                        source_type=SRC_ONION, http_status=resp.status,
                    )

                    # Update onion status in registry
                    await self._db.update_onion_status(domain, "alive", title)

                    # Discover and register new onion addresses
                    await self._process_discovered_onions(onions, url, depth)

                    # Enqueue same-domain links (depth limited)
                    if depth < MAX_DEPTH:
                        for link in links:
                            p = urlparse(link)
                            if p.netloc == domain:
                                await self._db.enqueue(
                                    link, SRC_ONION, priority=5, depth=depth + 1,
                                    referrer=url,
                                )

                    log.info(
                        f"[onion] ✓ {domain} | {len(text)} chars "
                        f"| {len(onions)} onions | depth={depth}"
                    )
                else:
                    log.debug(f"[onion] HTTP {resp.status} for {url}")
                    if resp.status in (404, 400):
                        await self._db.update_onion_status(domain, "dead")

            await self._db.mark_done(url, success=True)

        except asyncio.TimeoutError:
            log.debug(f"[onion] timeout: {url}")
            await self._db.mark_done(url, success=False)
            await self._db.update_onion_status(domain, "timeout")
        except Exception as exc:
            log.debug(f"[onion] error {url}: {exc}")
            await self._db.mark_done(url, success=False)

    async def _process_discovered_onions(
        self, onions: list, referrer: str, current_depth: int
    ) -> None:
        """Register new .onion addresses and enqueue them."""
        for onion in onions:
            # Build crawlable URL from bare address
            if not onion.startswith("http"):
                onion_url = f"http://{onion}/"
            else:
                onion_url = onion
                onion = urlparse(onion).netloc

            is_new = await self._db.register_onion(onion, tags=["discovered"])
            if is_new:
                log.info(f"[onion] 🧅 NEW onion discovered: {onion}")
                # Enqueue for crawling (lower priority than seeds)
                await self._db.enqueue(
                    onion_url, SRC_ONION, priority=4,
                    depth=current_depth + 1, referrer=referrer,
                )

    async def _rate_limit(self, domain: str) -> None:
        """Sleep if needed to respect per-domain rate limit."""
        async with self._lock:
            last = self._domain_ts.get(domain, 0)
            wait = RATE_LIMIT_SEC - (time.time() - last)
            if wait > 0:
                self._domain_ts[domain] = time.time() + wait
            else:
                self._domain_ts[domain] = time.time()
        if wait > 0:
            await asyncio.sleep(wait)
