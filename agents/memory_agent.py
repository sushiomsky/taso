"""
TASO – MemoryAgent

Central knowledge management agent.

Responsibilities:
  • receive store requests from other agents
  • write to vector store (semantic search)
  • write structured data to KnowledgeDB
  • answer memory queries via semantic search

Bus topics consumed:
  memory.store        – store a text chunk with category + metadata
  memory.store_cve    – store a CVE record
  memory.query        – semantic search query
  memory.audit        – write an audit log entry

Bus topics published:
  coordinator.result.<task_id>
"""

from __future__ import annotations

from typing import Any, Dict, List

from agents.base_agent import BaseAgent
from agents.message_bus import BusMessage, MessageBus
from memory.knowledge_db import KnowledgeDB
from memory.vector_store import VectorStore
from memory.conversation_store import ConversationStore
from config.logging_config import get_logger

log = get_logger("agent")


class MemoryAgent(BaseAgent):
    name = "memory"
    description = "Knowledge storage, retrieval, and vector-semantic search."

    def __init__(self, bus: MessageBus,
                 db: KnowledgeDB,
                 vector_store: VectorStore,
                 conv_store: ConversationStore) -> None:
        super().__init__(bus)
        self._db    = db
        self._vs    = vector_store
        self._conv  = conv_store

    async def _register_subscriptions(self) -> None:
        self._bus.subscribe("memory.store",     self._handle_store)
        self._bus.subscribe("memory.store_cve", self._handle_store_cve)
        self._bus.subscribe("memory.query",     self._handle_query)
        self._bus.subscribe("memory.audit",     self._handle_audit)

    # ------------------------------------------------------------------
    # Handlers
    # ------------------------------------------------------------------

    async def _handle_store(self, msg: BusMessage) -> None:
        """
        Payload: { category, text, metadata }
        Store the text in both the vector store and as an analysis result.
        """
        category = msg.payload.get("category", "general")
        text     = msg.payload.get("text", "")
        metadata = msg.payload.get("metadata", {})

        if not text:
            return

        # Vector store
        entry_id = self._vs.add(text, category=category, metadata=metadata)

        # Structured DB
        await self._db.insert_analysis(
            target=metadata.get("repo", metadata.get("task_id", "unknown")),
            agent=msg.sender,
            result_type=category,
            summary=text[:500],
            detail=metadata,
        )

        log.debug(f"MemoryAgent: stored [{category}] vector_id={entry_id}")

    async def _handle_store_cve(self, msg: BusMessage) -> None:
        """Payload: CVE dict from ResearchAgent."""
        p = msg.payload
        cve_id    = p.get("cve_id", "")
        if not cve_id:
            return

        await self._db.upsert_cve(
            cve_id=cve_id,
            description=p.get("description", ""),
            severity=p.get("severity", "UNKNOWN"),
            cvss_score=float(p.get("cvss_score", 0)),
            published=p.get("published", ""),
            modified=p.get("modified", ""),
            source=p.get("source", ""),
            raw=p.get("raw", {}),
        )

        # Also add to vector store for semantic search
        if p.get("description"):
            self._vs.add(
                f"{cve_id}: {p['description']}",
                category="cve",
                metadata={"cve_id": cve_id, "severity": p.get("severity", "")},
            )

        log.debug(f"MemoryAgent: stored CVE {cve_id}")

    async def _handle_query(self, msg: BusMessage) -> None:
        """
        Payload: { query, top_k, category, task_id }
        Perform semantic + keyword search and reply.
        """
        query    = msg.payload.get("query", "")
        top_k    = int(msg.payload.get("top_k", 5))
        category = msg.payload.get("category")
        task_id  = msg.payload.get("task_id", "")

        if not query:
            await self._reply(msg, {"task_id": task_id, "results": []})
            return

        # Semantic results
        vector_results = self._vs.search(query, top_k=top_k, category=category)

        # Keyword CVE search
        cve_results = await self._db.search_cves(query, limit=top_k)

        result = {
            "task_id":        task_id,
            "query":          query,
            "vector_results": vector_results,
            "cve_results":    cve_results,
        }
        await self._reply(msg, result)

    async def _handle_audit(self, msg: BusMessage) -> None:
        """Payload: { actor, action, target, status, detail }"""
        await self._db.audit(
            actor=msg.payload.get("actor", msg.sender),
            action=msg.payload.get("action", ""),
            target=msg.payload.get("target", ""),
            status=msg.payload.get("status", "ok"),
            detail=msg.payload.get("detail"),
        )

    # ------------------------------------------------------------------
    # Status / stats
    # ------------------------------------------------------------------

    async def get_stats(self) -> Dict[str, Any]:
        db_stats = await self._db.stats()
        return {
            "db":           db_stats,
            "vector_count": self._vs.count(),
        }

    async def handle(self, description: str, context: str = "") -> str:
        """Direct callable for swarm use — knowledge retrieval."""
        return await self.llm_query(description)

    # ------------------------------------------------------------------
    # Helpers
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
