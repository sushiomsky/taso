"""
Clearnet crawler unit tests.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest


class _FakeResponse:
    def __init__(self, status: int, body: str) -> None:
        self.status = status
        self._body = body

    async def text(self, errors: str = "replace") -> str:  # noqa: ARG002
        return self._body


class _FakeContext:
    def __init__(self, response: _FakeResponse) -> None:
        self._response = response

    async def __aenter__(self) -> _FakeResponse:
        return self._response

    async def __aexit__(self, exc_type, exc, tb) -> None:  # noqa: ANN001
        return None


class _FakeSession:
    def __init__(self, status: int, body: str) -> None:
        self._status = status
        self._body = body

    def get(self, *args, **kwargs):  # noqa: ANN002, ANN003
        return _FakeContext(_FakeResponse(self._status, self._body))


@pytest.mark.asyncio
async def test_robots_disallow_blocks_crawl():
    from crawler.clearnet_crawler import ClearnetCrawler

    crawler = ClearnetCrawler(MagicMock())
    session = _FakeSession(
        200,
        "User-agent: *\nDisallow: /private\n",
    )
    allowed = await crawler._is_allowed_by_robots(
        session,
        "https://example.com/private/data",
    )
    assert allowed is False


@pytest.mark.asyncio
async def test_robots_fetch_failure_defaults_to_allow():
    from crawler.clearnet_crawler import ClearnetCrawler

    crawler = ClearnetCrawler(MagicMock())
    session = _FakeSession(500, "")
    allowed = await crawler._is_allowed_by_robots(
        session,
        "https://example.com/anything",
    )
    assert allowed is True
