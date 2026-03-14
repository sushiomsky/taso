"""
TASO – ResearchAgent

Collects, parses, and stores threat intelligence from public sources.

Sources:
  • NVD CVE feed (REST API v2)
  • CISA Known Exploited Vulnerabilities catalogue
  • Tor-accessible forums (SOCKS5 proxy) – when TOR_ENABLED=true

Bus topics consumed:
  research.threat_intel   – start a collection run
  research.search_cve     – search for a specific CVE or keyword

Bus topics published:
  coordinator.result.<task_id>
  memory.store
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import aiohttp

from agents.base_agent import BaseAgent
from agents.message_bus import BusMessage, MessageBus
from config.settings import settings
from config.logging_config import get_logger

log = get_logger("agent")


def _tor_connector() -> Optional[aiohttp.TCPConnector]:
    """Return a SOCKS5-capable connector if aiohttp-socks is available."""
    if not settings.TOR_ENABLED:
        return None
    try:
        from aiohttp_socks import ProxyConnector
        return ProxyConnector.from_url(
            f"socks5://{settings.TOR_SOCKS_HOST}:{settings.TOR_SOCKS_PORT}"
        )
    except ImportError:
        log.warning("aiohttp-socks not installed – Tor support disabled.")
        return None


class ResearchAgent(BaseAgent):
    name = "research"
    description = "Threat intelligence collection from NVD, CISA, and Tor forums."

    SYSTEM_PROMPT = (
        "You are a threat intelligence analyst. Analyse vulnerability data, "
        "identify trends, assess risk, and provide concise actionable summaries."
    )

    async def _register_subscriptions(self) -> None:
        self._bus.subscribe("research.threat_intel", self._handle_threat_intel)
        self._bus.subscribe("research.search_cve",   self._handle_search_cve)

    # ------------------------------------------------------------------
    # Handlers
    # ------------------------------------------------------------------

    async def _handle_threat_intel(self, msg: BusMessage) -> None:
        task_id  = msg.payload.get("task_id", "")
        keywords = msg.payload.get("keywords", [])
        sources  = msg.payload.get("sources", ["nvd", "cisa"])

        log.info(f"ResearchAgent: threat_intel sources={sources} keywords={keywords}")

        gathered: Dict[str, Any] = {}

        if "nvd" in sources:
            gathered["nvd"] = await self._fetch_nvd(keywords)

        if "cisa" in sources:
            gathered["cisa"] = await self._fetch_cisa_kev()

        if "tor" in sources and settings.TOR_ENABLED:
            gathered["tor"] = await self._crawl_tor(keywords)

        # Store individual CVEs
        nvd_items = gathered.get("nvd", {}).get("items", [])
        for item in nvd_items[:50]:
            await self._bus.publish(
                BusMessage(
                    topic="memory.store_cve",
                    sender=self.name,
                    payload=item,
                )
            )

        summary = await self._llm_summarise(gathered)

        result = {
            "task_id":  task_id,
            "gathered": gathered,
            "summary":  summary,
        }
        await self._reply(msg, result)

    async def _handle_search_cve(self, msg: BusMessage) -> None:
        query   = msg.payload.get("query", "")
        task_id = msg.payload.get("task_id", "")

        log.info(f"ResearchAgent: search_cve query={query!r}")
        items = await self._fetch_nvd([query], results_per_page=10)
        result = {"task_id": task_id, "query": query, "results": items}
        await self._reply(msg, result)

    # ------------------------------------------------------------------
    # Data collection
    # ------------------------------------------------------------------

    async def _fetch_nvd(
        self,
        keywords: List[str],
        results_per_page: int = 20,
    ) -> Dict[str, Any]:
        """Fetch CVEs from the NVD REST API v2."""
        url    = settings.NVD_FEED_URL
        params: Dict[str, Any] = {"resultsPerPage": results_per_page}

        if keywords:
            params["keywordSearch"] = " ".join(keywords)
        if settings.NVD_API_KEY:
            params["apiKey"] = settings.NVD_API_KEY

        headers = {"User-Agent": "TASO/1.0 (security research)"}

        try:
            async with aiohttp.ClientSession(headers=headers) as session:
                async with session.get(
                    url, params=params,
                    timeout=aiohttp.ClientTimeout(total=30),
                ) as resp:
                    resp.raise_for_status()
                    raw = await resp.json()
        except Exception as exc:
            return {"error": str(exc), "items": []}

        items = []
        for vuln in raw.get("vulnerabilities", []):
            cve = vuln.get("cve", {})
            metrics = cve.get("metrics", {})

            # Extract CVSS score (v3.1 preferred, then v3.0, then v2)
            cvss_score = 0.0
            severity   = "UNKNOWN"
            for key in ("cvssMetricV31", "cvssMetricV30", "cvssMetricV2"):
                entries = metrics.get(key, [])
                if entries:
                    data = entries[0].get("cvssData", {})
                    cvss_score = data.get("baseScore", 0.0)
                    severity   = data.get("baseSeverity", "UNKNOWN")
                    break

            descs = cve.get("descriptions", [])
            desc  = next((d["value"] for d in descs if d["lang"] == "en"), "")

            items.append({
                "cve_id":      cve.get("id", ""),
                "description": desc,
                "severity":    severity,
                "cvss_score":  cvss_score,
                "published":   cve.get("published", ""),
                "modified":    cve.get("lastModified", ""),
                "source":      "nvd",
                "raw":         cve,
            })

        return {"total": raw.get("totalResults", 0), "items": items}

    async def _fetch_cisa_kev(self) -> Dict[str, Any]:
        """Fetch CISA Known Exploited Vulnerabilities catalogue."""
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    settings.CISA_KEV_URL,
                    timeout=aiohttp.ClientTimeout(total=30),
                ) as resp:
                    resp.raise_for_status()
                    data = await resp.json(content_type=None)
        except Exception as exc:
            return {"error": str(exc), "items": []}

        items = []
        for v in data.get("vulnerabilities", [])[:50]:
            items.append({
                "cve_id":      v.get("cveID", ""),
                "product":     v.get("product", ""),
                "vendor":      v.get("vendorProject", ""),
                "description": v.get("shortDescription", ""),
                "due_date":    v.get("dueDate", ""),
                "added":       v.get("dateAdded", ""),
            })

        return {
            "catalogue_version": data.get("catalogVersion", ""),
            "count":             data.get("count", 0),
            "items":             items,
        }

    async def _crawl_tor(self, keywords: List[str]) -> Dict[str, Any]:
        """
        Crawl Tor-accessible security resources via SOCKS5.

        Currently queries the Tor project's onion index for demonstration.
        Replace URLs with actual threat intel onion sites as appropriate.
        """
        connector = _tor_connector()
        if not connector:
            return {"error": "Tor connector unavailable", "items": []}

        # Example: fetch a public .onion threat feed (placeholder)
        # Replace with verified threat intel sources
        test_url = "http://check.torproject.org"
        try:
            async with aiohttp.ClientSession(connector=connector) as session:
                async with session.get(
                    test_url, timeout=aiohttp.ClientTimeout(total=30)
                ) as resp:
                    text = await resp.text()
                    return {
                        "status": resp.status,
                        "tor_reachable": "Congratulations" in text,
                        "items": [],
                    }
        except Exception as exc:
            return {"error": str(exc), "items": []}

    # ------------------------------------------------------------------
    # LLM summary
    # ------------------------------------------------------------------

    async def _llm_summarise(self, data: Dict) -> str:
        text = json.dumps(data, indent=2)[:5000]
        prompt = (
            f"Threat intelligence data collected:\n{text}\n\n"
            "Provide: 1) top 3 critical vulnerabilities to prioritise, "
            "2) emerging threat trends, 3) recommended defensive actions."
        )
        return await self.llm_query(prompt, system=self.SYSTEM_PROMPT)

    async def handle(self, description: str, context: str = "") -> str:
        """Direct callable for swarm use — threat research."""
        prompt = description
        if context:
            prompt = f"{context}\n\nTask: {description}"
        return await self.llm_query(prompt, system=self.SYSTEM_PROMPT)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _reply(self, msg: BusMessage, result: Dict) -> None:
        if msg.reply_to:
            await self._bus.publish(
                BusMessage(
                    topic=msg.reply_to,
                    sender=self.name,
                    recipient=msg.sender,
                    payload=result,
                )
            )
        # Store summary in vector store
        await self._bus.publish(
            BusMessage(
                topic="memory.store",
                sender=self.name,
                payload={
                    "category": "threat_intel",
                    "text":     result.get("summary", ""),
                    "metadata": {"task_id": result.get("task_id", "")},
                },
            )
        )
