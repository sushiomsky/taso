"""
Crawler orchestration tests (onion + manager + bot wiring surface).
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@pytest.mark.asyncio
async def test_onion_process_discovered_onions_registers_and_queues():
    from crawler.onion_crawler import OnionCrawler

    db = MagicMock()
    db.register_onion = AsyncMock(return_value=True)
    db.enqueue = AsyncMock(return_value=True)

    crawler = OnionCrawler(db)
    await crawler._process_discovered_onions(
        ["exampleexample.onion"],
        referrer="http://seed.onion/",
        current_depth=1,
    )

    db.register_onion.assert_called_once()
    db.enqueue.assert_called_once()
    args, _kwargs = db.enqueue.call_args
    assert "exampleexample.onion" in args[0]


@pytest.mark.asyncio
async def test_crawler_manager_add_url_onion_path():
    from crawler.crawler_manager import CrawlerManager

    cm = CrawlerManager()
    cm._db = MagicMock()
    cm._db.register_onion = AsyncMock(return_value=True)
    cm._db.enqueue = AsyncMock(return_value=True)

    with patch.object(cm, "connect", new_callable=AsyncMock):
        msg = await cm.add_url("exampleexample.onion")

    assert "Added to crawl queue" in msg
    cm._db.register_onion.assert_called_once()
    cm._db.enqueue.assert_called_once()


@pytest.mark.asyncio
async def test_crawler_manager_status_shape():
    from crawler.crawler_manager import CrawlerManager

    cm = CrawlerManager()
    cm._db = MagicMock()
    cm._db.global_stats = AsyncMock(return_value={"queue": {"pending": 1}})

    with patch.object(cm, "connect", new_callable=AsyncMock):
        status = await cm.status()

    assert "crawlers" in status
    assert "db" in status
