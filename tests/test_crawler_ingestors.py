"""
Crawler ingestor and extractor tests.
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def test_text_extractor_extracts_links_and_onions():
    from crawler.text_extractor import extract

    html = b"""
    <html>
      <head><title>Security News</title><script>var secret='x';</script></head>
      <body>
        <a href="/advisory">Advisory</a>
        <a href="https://example.com/file.jpg">Image</a>
        threat mirror: testtesttesttest.onion
      </body>
    </html>
    """
    title, text, links, onions = extract(html, base_url="https://example.com")

    assert "Security News" in title
    assert "secret" not in text
    assert "https://example.com/advisory" in links
    assert "https://example.com/file.jpg" not in links
    assert "testtesttesttest.onion" in onions


@pytest.mark.asyncio
async def test_irc_indexer_start_stop_without_network():
    from crawler.irc_indexer import IRCIndexer

    db = MagicMock()

    with patch("crawler.irc_indexer.IRC_TARGETS", [{
        "network": "TestNet",
        "host": "localhost",
        "port": 6667,
        "tls": False,
        "channels": ["#security"],
    }]), patch("crawler.irc_indexer.IRCClient.run", new_callable=AsyncMock):
        idx = IRCIndexer(db)
        await idx.start()
        assert idx.is_running is True
        await idx.stop()
        assert idx.is_running is False


@pytest.mark.asyncio
async def test_newsgroup_fetch_now_delegates():
    from crawler.newsgroup_indexer import NewsgroupIndexer

    db = MagicMock()
    idx = NewsgroupIndexer(db)

    with patch.object(idx, "_fetch_all", new_callable=AsyncMock) as fetch_all:
        fetch_all.return_value = 5
        count = await idx.fetch_now()

    assert count == 5
