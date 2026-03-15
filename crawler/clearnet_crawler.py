"""
TASO Crawler — Clearnet Security Site Crawler

Crawls publicly accessible security/hacking/cybercrime research websites.
Stays domain-scoped (does not spider off to unrelated sites).
Respects a configurable politeness delay.
"""
from __future__ import annotations

import asyncio
import time
from typing import Set, Optional
from urllib.parse import urlparse

from config.logging_config import get_logger
from crawler.crawler_db import CrawlerDB, SRC_CLEARNET, SRC_MANUAL
from crawler.text_extractor import extract, extract_onions_from_text
from crawler.seed_urls import CLEARNET_SEEDS

try:
    import aiohttp
    _AIOHTTP = True
except ImportError:
    _AIOHTTP = False

log = get_logger("clearnet_crawler")

REQUEST_TIMEOUT = 20
MAX_CONTENT     = 1_000_000   # 1 MB
RATE_LIMIT_SEC  = 2.0          # seconds between requests to same domain
MAX_DEPTH       = 3
BATCH_SIZE      = 10
MAX_WORKERS     = 5

# Allowed domains — crawler stays within these (expanded as new URLs are manually added)
# This prevents accidentally crawling half the internet.
_ALLOWED_DOMAINS: Set[str] = {urlparse(u).netloc for u, _, _ in CLEARNET_SEEDS if u.startswith("http")}

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (compatible; TASO-SecurityResearch/1.0; "
        "+https://github.com/sushiomsky/taso)"
    ),
    "Accept": "text/html,application/xhtml+xml,text/plain;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}


class ClearnetCrawler:
    """
    Async BFS crawler for clearnet security sites.

    Only crawls domains present in the allow-list. New domains can be
    added at runtime via add_domain().
    """

    def __init__(self, db: CrawlerDB) -> None:
        self._db       = db
        self._running  = False
        self._tasks: Set[asyncio.Task] = set()
        self._domain_ts: dict[str, float] = {}
        self._allowed  = set(_ALLOWED_DOMAINS)
        self._lock     = asyncio.Lock()

    # ------------------------------------------------------------------ #
    # Public interface
    # ------------------------------------------------------------------ #

    async def start(self) -> None:
        if not _AIOHTTP:
            log.error("aiohttp not installed — clearnet crawler disabled")
            return

        log.info("ClearnetCrawler starting…")
        self._running = True

        # Seed the queue
        for url, src, pri in CLEARNET_SEEDS:
            await self._db.enqueue(url, src, priority=pri, depth=0)

        for i in range(MAX_WORKERS):
            t = asyncio.create_task(self._worker(i))
            self._tasks.add(t)
            t.add_done_callback(self._tasks.discard)

        log.info(f"ClearnetCrawler: {MAX_WORKERS} workers started")

    async def stop(self) -> None:
        self._running = False
        for t in list(self._tasks):
            t.cancel()
        log.info("ClearnetCrawler stopped")

    async def add_url(self, url: str, priority: int = 8) -> bool:
        """Manually enqueue a URL and add its domain to the allow-list."""
        domain = urlparse(url).netloc
        async with self._lock:
            self._allowed.add(domain)
        ok = await self._db.enqueue(url, SRC_MANUAL, priority=priority, depth=0)
        log.info(f"Manually added URL: {url} (new={ok})")
        return ok

    def add_domain(self, domain: str) -> None:
        """Add a domain to the crawl allow-list."""
        self._allowed.add(domain.lstrip("www."))

    @property
    def is_running(self) -> bool:
        return self._running

    # ------------------------------------------------------------------ #
    # Worker
    # ------------------------------------------------------------------ #

    async def _worker(self, worker_id: int) -> None:
        log.debug(f"Clearnet worker {worker_id} started")
        connector = aiohttp.TCPConnector(limit=20, ssl=False)
        timeout   = aiohttp.ClientTimeout(total=REQUEST_TIMEOUT, connect=10)
        session   = aiohttp.ClientSession(
            connector=connector, timeout=timeout, headers=HEADERS
        )

        try:
            while self._running:
                # Pull from both clearnet and manual source types
                batch = await self._db.dequeue_batch(SRC_CLEARNET, BATCH_SIZE // 2)
                batch += await self._db.dequeue_batch(SRC_MANUAL, BATCH_SIZE // 2)
                if not batch:
                    await asyncio.sleep(5)
                    continue

                for item in batch:
                    if not self._running:
                        break
                    await self._crawl_one(session, item["url"], item["depth"])
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            log.exception(f"Clearnet worker {worker_id} fatal error: {exc}")
        finally:
            await session.close()
            await connector.close()

    async def _crawl_one(
        self, session: "aiohttp.ClientSession", url: str, depth: int
    ) -> None:
        domain = urlparse(url).netloc
        # Domain guard
        base_domain = domain.lstrip("www.")
        if not any(base_domain == d.lstrip("www.") or base_domain.endswith("." + d.lstrip("www."))
                   for d in self._allowed):
            await self._db.mark_done(url, success=True)
            return

        await self._rate_limit(domain)

        try:
            log.debug(f"[clearnet] fetching {url}")
            async with session.get(url, allow_redirects=True, max_redirects=5) as resp:
                if resp.status != 200:
                    await self._db.mark_done(url, success=(resp.status < 500))
                    return

                ct = resp.headers.get("Content-Type", "")
                if "text" not in ct:
                    await self._db.mark_done(url, success=True)
                    return

                raw = await resp.read()
                if len(raw) > MAX_CONTENT:
                    raw = raw[:MAX_CONTENT]

                # Detect encoding from Content-Type
                enc = "utf-8"
                if "charset=" in ct:
                    enc = ct.split("charset=")[-1].split(";")[0].strip()

                title, text, links, onions = extract(raw, base_url=url, encoding=enc)

                await self._db.save_page(
                    url=url, title=title, text_body=text,
                    source_type=SRC_CLEARNET, http_status=200,
                )

                # Register any .onion addresses found on clearnet pages
                for onion in onions:
                    is_new = await self._db.register_onion(
                        onion, tags=["clearnet-discovered"]
                    )
                    if is_new:
                        log.info(f"[clearnet] 🧅 .onion found: {onion} on {url}")
                        await self._db.enqueue(
                            f"http://{onion}/", SRC_ONION, priority=7,
                            depth=0, referrer=url,
                        )

                # Enqueue same-domain links within depth limit
                if depth < MAX_DEPTH:
                    for link in links:
                        p = urlparse(link)
                        link_domain = p.netloc.lstrip("www.")
                        if link_domain == base_domain:
                            await self._db.enqueue(
                                link, SRC_CLEARNET, priority=4,
                                depth=depth + 1, referrer=url,
                            )

                log.info(
                    f"[clearnet] ✓ {domain} | "
                    f"{len(text)} chars | {len(onions)} onions | depth={depth}"
                )

            await self._db.mark_done(url, success=True)

        except asyncio.TimeoutError:
            log.debug(f"[clearnet] timeout: {url}")
            await self._db.mark_done(url, success=False)
        except aiohttp.ClientError as exc:
            log.debug(f"[clearnet] client error {url}: {exc}")
            await self._db.mark_done(url, success=False)
        except Exception as exc:
            log.debug(f"[clearnet] error {url}: {exc}")
            await self._db.mark_done(url, success=False)

    async def _rate_limit(self, domain: str) -> None:
        async with self._lock:
            last = self._domain_ts.get(domain, 0)
            wait = RATE_LIMIT_SEC - (time.time() - last)
            self._domain_ts[domain] = time.time() + max(wait, 0)
        if wait > 0:
            await asyncio.sleep(wait)
