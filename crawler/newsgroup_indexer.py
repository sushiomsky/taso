"""
TASO Crawler — Newsgroup (Usenet/NNTP) Indexer

Connects to a public NNTP server, fetches articles from security-related
newsgroups, and stores the text content in the crawler database.

Uses Python's built-in nntplib (synchronous) wrapped in asyncio executor.
"""
from __future__ import annotations

import asyncio
import email
import email.header
import nntplib
import time
from datetime import datetime, timedelta, timezone
from typing import List, Optional, Dict

from config.logging_config import get_logger
from crawler.crawler_db import CrawlerDB
from crawler.seed_urls import NEWSGROUP_TARGETS, NNTP_SERVER, NNTP_PORT, NNTP_MAX_AGE_DAYS
from crawler.text_extractor import extract_onions_from_text

log = get_logger("newsgroup_indexer")

FETCH_INTERVAL_HOURS = 6   # re-check newsgroups every 6 hours
MAX_ARTICLES_PER_GROUP = 500


def _decode_header(value: str) -> str:
    """Decode RFC2047 encoded MIME headers to plain string."""
    if not value:
        return ""
    parts = []
    for decoded, charset in email.header.decode_header(value):
        if isinstance(decoded, bytes):
            parts.append(decoded.decode(charset or "utf-8", errors="replace"))
        else:
            parts.append(str(decoded))
    return " ".join(parts)


def _fetch_group(server: str, port: int, group: str, since_days: int) -> List[Dict]:
    """
    Blocking NNTP fetch — runs in a thread executor.
    Returns list of article dicts: message_id, subject, author, body, posted_at.
    """
    articles = []
    try:
        nntp = nntplib.NNTP(server, port, timeout=30)
        resp, count, first, last, name = nntp.group(group)
        log.debug(f"[nntp] {group}: {count} articles (first={first}, last={last})")

        # Compute article range — only fetch recent ones
        cutoff = datetime.now(timezone.utc) - timedelta(days=since_days)
        since_str = cutoff.strftime("%Y%m%d %H%M%S")

        try:
            # NEWNEWS command — get message IDs since date
            _, new_ids = nntp.newnews(group, cutoff)
        except nntplib.NNTPError:
            # Fallback: fetch tail of the group
            fetch_count = min(MAX_ARTICLES_PER_GROUP, int(last) - int(first) + 1)
            start = max(int(first), int(last) - fetch_count + 1)
            _, new_ids_raw = nntp.over((str(start), str(last)))
            new_ids = [row[1].message_id for row in new_ids_raw if hasattr(row[1], 'message_id')]

        # Limit to avoid overload
        new_ids = new_ids[:MAX_ARTICLES_PER_GROUP]

        for mid in new_ids:
            try:
                _, info = nntp.article(mid)
                raw_lines = info.lines
                raw_bytes  = b"\r\n".join(raw_lines)
                msg        = email.message_from_bytes(raw_bytes)

                subject  = _decode_header(msg.get("Subject", ""))
                author   = _decode_header(msg.get("From", ""))
                date_str = msg.get("Date", "")

                # Parse date
                try:
                    posted_at = email.utils.parsedate_to_datetime(date_str).timestamp()
                except Exception:
                    posted_at = time.time()

                # Extract plain text body
                body_parts = []
                if msg.is_multipart():
                    for part in msg.walk():
                        if part.get_content_type() == "text/plain":
                            payload = part.get_payload(decode=True)
                            if payload:
                                charset = part.get_content_charset() or "utf-8"
                                body_parts.append(
                                    payload.decode(charset, errors="replace")
                                )
                else:
                    payload = msg.get_payload(decode=True)
                    if payload:
                        charset = msg.get_content_charset() or "utf-8"
                        body_parts.append(payload.decode(charset, errors="replace"))

                body = "\n".join(body_parts)[:50_000]  # cap at 50 KB

                articles.append({
                    "message_id": mid,
                    "subject":    subject,
                    "author":     author,
                    "body":       body,
                    "posted_at":  posted_at,
                })
            except Exception as exc:
                log.debug(f"[nntp] skip article {mid}: {exc}")
                continue

        nntp.quit()
        log.info(f"[nntp] {group}: fetched {len(articles)} articles")
    except nntplib.NNTPTemporaryError as exc:
        log.warning(f"[nntp] temp error {group}: {exc}")
    except nntplib.NNTPError as exc:
        log.warning(f"[nntp] error {group}: {exc}")
    except Exception as exc:
        log.warning(f"[nntp] unexpected error {group}: {exc}")

    return articles


class NewsgroupIndexer:
    """
    Periodically fetches articles from configured Usenet newsgroups
    and stores them in the crawler DB.
    """

    def __init__(self, db: CrawlerDB) -> None:
        self._db      = db
        self._running = False
        self._task:   Optional[asyncio.Task] = None

    async def start(self) -> None:
        log.info(f"NewsgroupIndexer starting ({len(NEWSGROUP_TARGETS)} groups)…")
        self._running = True
        self._task    = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
        log.info("NewsgroupIndexer stopped")

    @property
    def is_running(self) -> bool:
        return self._running

    async def fetch_now(self) -> int:
        """Manually trigger a full fetch. Returns total articles stored."""
        return await self._fetch_all()

    async def _loop(self) -> None:
        while self._running:
            try:
                count = await self._fetch_all()
                log.info(f"[nntp] cycle complete — {count} new articles indexed")
            except asyncio.CancelledError:
                break
            except Exception as exc:
                log.exception(f"[nntp] fetch cycle error: {exc}")
            # Wait before next cycle
            await asyncio.sleep(FETCH_INTERVAL_HOURS * 3600)

    async def _fetch_all(self) -> int:
        total = 0
        loop  = asyncio.get_event_loop()

        for group in NEWSGROUP_TARGETS:
            if not self._running:
                break
            try:
                articles = await loop.run_in_executor(
                    None, _fetch_group, NNTP_SERVER, NNTP_PORT, group, NNTP_MAX_AGE_DAYS
                )
                for art in articles:
                    is_new = await self._db.save_newsgroup_post(
                        message_id=art["message_id"],
                        newsgroup=group,
                        subject=art["subject"],
                        author=art["author"],
                        body=art["body"],
                        posted_at=art["posted_at"],
                    )
                    if is_new:
                        total += 1
                        # Check body for .onion addresses
                        for onion in extract_onions_from_text(art["body"]):
                            is_new_onion = await self._db.register_onion(
                                onion, tags=["newsgroup-discovered"]
                            )
                            if is_new_onion:
                                log.info(f"[nntp] 🧅 .onion in {group}: {onion}")
                                await self._db.enqueue(
                                    f"http://{onion}/", "onion", priority=5, depth=0,
                                    referrer=f"nntp://{group}",
                                )
            except Exception as exc:
                log.warning(f"[nntp] error processing {group}: {exc}")

        return total
