"""
Crawler core regression tests (DB + seeds).
"""
from __future__ import annotations

import pytest


@pytest.mark.asyncio
async def test_crawler_db_queue_and_search(tmp_path):
    from crawler.crawler_db import CrawlerDB, SRC_CLEARNET

    db = CrawlerDB(db_path=str(tmp_path / "crawler.db"))
    await db.connect()
    try:
        added = await db.enqueue(
            "https://example.com/security",
            source_type=SRC_CLEARNET,
            priority=9,
            depth=0,
        )
        assert added is True

        batch = await db.dequeue_batch(SRC_CLEARNET, batch=1)
        assert len(batch) == 1
        assert batch[0]["url"] == "https://example.com/security"

        await db.save_page(
            url="https://example.com/security",
            title="Example Security Page",
            text_body="this page contains security advisories",
            source_type=SRC_CLEARNET,
            http_status=200,
        )

        results = await db.search("security", limit=5)
        assert any(r.get("type") == "page" for r in results)
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_crawler_db_register_onion(tmp_path):
    from crawler.crawler_db import CrawlerDB

    db = CrawlerDB(db_path=str(tmp_path / "crawler.db"))
    await db.connect()
    try:
        is_new = await db.register_onion("exampleexample.onion", tags=["test"])
        assert is_new is True
        assert await db.count_onions() == 1

        is_new_again = await db.register_onion("exampleexample.onion", tags=["test"])
        assert is_new_again is False
        assert await db.count_onions() == 1
    finally:
        await db.close()


def test_seed_lists_have_expected_content():
    from crawler.seed_urls import CLEARNET_SEEDS, ONION_SEEDS, IRC_TARGETS, NEWSGROUP_TARGETS

    assert len(CLEARNET_SEEDS) > 0
    assert len(ONION_SEEDS) > 0
    assert len(IRC_TARGETS) > 0
    assert len(NEWSGROUP_TARGETS) > 0
