"""
TASO Crawler — Text Extractor

Converts raw HTML into clean plain text and extracts all links,
including .onion addresses.
"""
from __future__ import annotations

import re
from typing import List, Tuple, Optional
from urllib.parse import urljoin, urlparse, urlunparse

# Optional deps — graceful degradation
try:
    from bs4 import BeautifulSoup
    _BS4 = True
except ImportError:
    _BS4 = False

try:
    import html2text as _h2t
    _HTML2TEXT = True
except ImportError:
    _HTML2TEXT = False

from config.logging_config import get_logger

log = get_logger("text_extractor")

# .onion v2 (16 chars) and v3 (56 chars) regex
_ONION_RE = re.compile(
    r'\b([a-z2-7]{16}|[a-z2-7]{56})\.onion\b', re.IGNORECASE
)

# Max text length per page stored in DB
MAX_TEXT_LEN = 200_000   # 200 KB of text
MAX_TITLE_LEN = 512

# Tags to completely drop content from
_DROP_TAGS = {
    "script", "style", "noscript", "iframe", "svg", "canvas",
    "head", "meta", "link", "button", "nav", "footer", "header",
}


def extract(
    html: bytes,
    base_url: str = "",
    encoding: str = "utf-8",
) -> Tuple[str, str, List[str], List[str]]:
    """
    Parse HTML and return:
      title     — page title (str)
      text      — clean plain text (str)
      links     — all absolute href links found (List[str])
      onions    — all .onion addresses found anywhere in the page (List[str])
    """
    try:
        raw = html.decode(encoding, errors="replace")
    except Exception:
        raw = html.decode("latin-1", errors="replace")

    # --- Extract .onion addresses from the raw HTML first (before stripping) ---
    onions = list({m.lower() + ".onion" for m in _ONION_RE.findall(raw)})

    title = ""
    text  = ""
    links: List[str] = []

    if _BS4:
        soup = BeautifulSoup(raw, "html.parser")

        # Title
        t = soup.find("title")
        if t:
            title = t.get_text(strip=True)[:MAX_TITLE_LEN]

        # Extract links
        for tag in soup.find_all("a", href=True):
            href = tag["href"].strip()
            if href and not href.startswith(("#", "javascript:", "mailto:")):
                if base_url:
                    href = urljoin(base_url, href)
                links.append(_normalize_url(href))

        # Remove noisy tags
        for tag in soup.find_all(_DROP_TAGS):
            tag.decompose()

        # Get text
        if _HTML2TEXT:
            h = _h2t.HTML2Text()
            h.ignore_links       = True
            h.ignore_images      = True
            h.ignore_emphasis    = False
            h.body_width         = 0
            h.unicode_snob       = True
            text = h.handle(str(soup))
        else:
            text = soup.get_text(separator="\n", strip=True)
    else:
        # Fallback: simple regex stripping
        title_m = re.search(r"<title[^>]*>(.*?)</title>", raw, re.IGNORECASE | re.DOTALL)
        if title_m:
            title = _strip_tags(title_m.group(1))[:MAX_TITLE_LEN]
        links = [
            urljoin(base_url, m)
            for m in re.findall(r'href=["\']([^"\']+)["\']', raw, re.IGNORECASE)
        ]
        text = _strip_tags(raw)

    # Normalise whitespace
    text = re.sub(r'\n{3,}', '\n\n', text).strip()[:MAX_TEXT_LEN]

    # Deduplicate + filter links
    links = list({_normalize_url(l) for l in links if _is_crawlable(l)})

    return title, text, links, onions


def _strip_tags(html: str) -> str:
    """Minimal regex-based tag stripper (fallback if BS4 not available)."""
    return re.sub(r"<[^>]+>", " ", html)


def _normalize_url(url: str) -> str:
    """Remove fragment, normalise scheme."""
    try:
        p = urlparse(url)
        return urlunparse((p.scheme.lower(), p.netloc.lower(), p.path, p.params, p.query, ""))
    except Exception:
        return url


def _is_crawlable(url: str) -> bool:
    """Return True if the URL is something worth crawling."""
    try:
        p = urlparse(url)
        if p.scheme not in ("http", "https"):
            return False
        # Skip common non-text resources
        path = p.path.lower()
        skip_ext = (
            ".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg", ".ico",
            ".pdf", ".zip", ".tar", ".gz", ".exe", ".dmg", ".apk",
            ".mp4", ".mp3", ".avi", ".mov", ".woff", ".woff2", ".ttf",
            ".css", ".js",
        )
        if any(path.endswith(ext) for ext in skip_ext):
            return False
        return True
    except Exception:
        return False


def extract_onions_from_text(text: str) -> List[str]:
    """Extract .onion addresses from plain text."""
    return list({m.lower() + ".onion" for m in _ONION_RE.findall(text)})
