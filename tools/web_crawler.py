"""
TASO – Tool: web_crawler

Crawls public URLs and extracts text content.
Supports Tor SOCKS5 proxy for .onion addresses.

Uses aiohttp for HTTP and html2text / BeautifulSoup for parsing.
"""

from __future__ import annotations

import asyncio
import re
from typing import Any, Dict, List, Optional
from urllib.parse import urljoin, urlparse

import aiohttp

from config.settings import settings
from tools.base_tool import BaseTool, ToolSchema

try:
    from bs4 import BeautifulSoup
    _BS4_OK = True
except ImportError:
    _BS4_OK = False


class WebCrawlerTool(BaseTool):
    name        = "web_crawler"
    description = "Crawl a URL and extract text content; supports Tor SOCKS5."
    schema      = ToolSchema({
        "url":       {"type": "str", "required": True,
                      "description": "URL to crawl."},
        "depth":     {"type": "int", "required": False, "default": 1,
                      "description": "Link-follow depth (1 = current page only)."},
        "use_tor":   {"type": "bool", "required": False, "default": False,
                      "description": "Route via Tor SOCKS5 proxy."},
        "max_pages": {"type": "int", "required": False, "default": 5,
                      "description": "Maximum pages to fetch."},
    })

    async def execute(self, url: str, depth: int = 1,
                       use_tor: bool = False, max_pages: int = 5,
                       **_: Any) -> Dict[str, Any]:
        connector = None
        if use_tor and settings.TOR_ENABLED:
            connector = self._tor_connector()

        visited: Dict[str, str] = {}
        await self._crawl(url, depth, max_pages, visited, connector)

        if connector:
            await connector.close()

        pages = []
        for page_url, content in visited.items():
            pages.append({
                "url":     page_url,
                "length":  len(content),
                "excerpt": content[:500],
                "text":    content,
            })

        return {
            "seed_url":    url,
            "pages_found": len(pages),
            "pages":       pages,
        }

    # ------------------------------------------------------------------

    async def _crawl(self, url: str, depth: int, max_pages: int,
                      visited: Dict[str, str],
                      connector: Optional[Any]) -> None:
        if len(visited) >= max_pages or url in visited:
            return

        content = await self._fetch(url, connector)
        if content is None:
            return

        visited[url] = content

        if depth <= 1:
            return

        # Extract links from this page
        links = self._extract_links(content, url)
        tasks = [
            self._crawl(link, depth - 1, max_pages, visited, connector)
            for link in links[: max_pages - len(visited)]
        ]
        await asyncio.gather(*tasks)

    async def _fetch(self, url: str,
                      connector: Optional[Any]) -> Optional[str]:
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (compatible; TASO-Security-Crawler/1.0; "
                "+https://github.com/example/taso)"
            )
        }
        try:
            kwargs: Dict[str, Any] = {
                "headers": headers,
                "timeout": aiohttp.ClientTimeout(total=20),
            }
            if connector:
                kwargs["connector"] = connector

            async with aiohttp.ClientSession(**kwargs) as session:
                async with session.get(url) as resp:
                    if resp.status != 200:
                        return None
                    html = await resp.text(errors="ignore")
                    return self._parse_html(html)
        except Exception:
            return None

    @staticmethod
    def _parse_html(html: str) -> str:
        if _BS4_OK:
            soup = BeautifulSoup(html, "html.parser")
            # Remove scripts, styles, nav
            for tag in soup(["script", "style", "nav", "footer", "header"]):
                tag.decompose()
            return soup.get_text(separator="\n", strip=True)

        # Fallback: strip tags with regex
        text = re.sub(r"<[^>]+>", " ", html)
        text = re.sub(r"\s+", " ", text)
        return text.strip()

    @staticmethod
    def _extract_links(content: str, base_url: str) -> List[str]:
        """Extract absolute HTTP links from page text (very basic)."""
        # We work on raw HTML – but since we already parsed, use regex on original
        # For now, return empty – would need original HTML saved to extract properly.
        # In production, use BeautifulSoup on raw HTML before stripping.
        return []

    @staticmethod
    def _tor_connector():
        try:
            from aiohttp_socks import ProxyConnector
            return ProxyConnector.from_url(
                f"socks5://{settings.TOR_SOCKS_HOST}:{settings.TOR_SOCKS_PORT}"
            )
        except ImportError:
            return None
