"""
Tests for the Memory subsystem.

Covers:
  - KnowledgeDB: connect, upsert_cve, get_cves, search_cves, insert_advisory
  - ConversationStore: connect, add_message, get_history
  - VersionManager (in-memory): record, mark_stable, latest_stable
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from memory.knowledge_db import KnowledgeDB
from memory.conversation_store import ConversationStore


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
async def db(tmp_path):
    d = KnowledgeDB(path=tmp_path / "test.db")
    await d.connect()
    yield d
    await d.close()


@pytest.fixture
async def store(tmp_path):
    s = ConversationStore(path=tmp_path / "conv.db")
    await s.connect()
    yield s
    await s.close()


# ---------------------------------------------------------------------------
# KnowledgeDB tests
# ---------------------------------------------------------------------------

async def test_knowledge_db_empty_on_connect(db):
    assert await db.get_cves() == []


async def test_knowledge_db_upsert_and_get(db):
    await db.upsert_cve(
        cve_id="CVE-2024-0001", description="Test vuln", severity="HIGH",
        cvss_score=8.5, published="2024-01-01", modified="2024-01-02",
        source="test", raw={"id": "CVE-2024-0001"},
    )
    cves = await db.get_cves()
    assert len(cves) == 1
    assert cves[0]["cve_id"] == "CVE-2024-0001"
    assert cves[0]["severity"] == "HIGH"


async def test_knowledge_db_upsert_is_idempotent(db):
    for _ in range(3):
        await db.upsert_cve(
            cve_id="CVE-2024-0002", description="Dupe", severity="MEDIUM",
            cvss_score=5.0, published="2024-01-01", modified="2024-01-03",
            source="test", raw={},
        )
    assert len(await db.get_cves()) == 1


async def test_knowledge_db_search(db):
    await db.upsert_cve(
        cve_id="CVE-2024-0010", description="Remote code execution in foo",
        severity="CRITICAL", cvss_score=9.8,
        published="2024-01-01", modified="2024-01-01", source="test", raw={},
    )
    assert len(await db.search_cves("remote code execution")) == 1
    assert len(await db.search_cves("SQL injection")) == 0


async def test_knowledge_db_severity_filter(db):
    for sev, cid in [("HIGH", "CVE-H1"), ("LOW", "CVE-L1"), ("CRITICAL", "CVE-C1")]:
        await db.upsert_cve(
            cve_id=cid, description=f"{sev}", severity=sev,
            cvss_score=1.0, published="2024-01-01", modified="2024-01-01",
            source="test", raw={},
        )
    high = await db.get_cves(severity="HIGH")
    assert all(c["severity"] == "HIGH" for c in high)


async def test_knowledge_db_advisory(db):
    row_id = await db.insert_advisory(
        title="Test Advisory", source="CISA", url="https://example.com",
        severity="HIGH", summary="A test advisory", raw="raw content",
    )
    assert isinstance(row_id, int) and row_id > 0
    advs = await db.get_advisories()
    assert len(advs) == 1
    assert advs[0]["title"] == "Test Advisory"


# ---------------------------------------------------------------------------
# ConversationStore tests
# ---------------------------------------------------------------------------

async def test_conversation_add_and_get(store):
    await store.add_message(12345, "user", "Hello bot")
    await store.add_message(12345, "assistant", "Hello human")
    history = await store.get_history(12345)
    assert len(history) == 2
    assert history[0]["role"] == "user"
    assert history[1]["role"] == "assistant"


async def test_conversation_isolated_chats(store):
    await store.add_message(1, "user", "Chat 1")
    await store.add_message(2, "user", "Chat 2")
    assert len(await store.get_history(1)) == 1
    assert len(await store.get_history(2)) == 1
    h1 = await store.get_history(1)
    h2 = await store.get_history(2)
    assert h1[0]["content"] != h2[0]["content"]


# ---------------------------------------------------------------------------
# VersionManager tests (in-memory, no I/O)
# ---------------------------------------------------------------------------

def test_version_record_created():
    from self_healing.version_manager import VersionManager
    vm = VersionManager()
    rec = vm.record(
        author_agent="test_agent", change_type="patch",
        description="Test patch", files_changed=["bot/telegram_bot.py"],
    )
    assert rec.version_id.startswith("v")
    assert rec.author_agent == "test_agent"
    assert not rec.stable
    assert not rec.deployed


def test_version_mark_stable():
    from self_healing.version_manager import VersionManager
    vm = VersionManager()
    rec = vm.record(author_agent="test", change_type="patch", description="Stable")
    vm.mark_stable(rec.version_id, commit_sha="abc123")
    assert rec.stable and rec.deployed and rec.commit_sha == "abc123"


def test_version_latest_stable():
    from self_healing.version_manager import VersionManager
    vm = VersionManager()
    r1 = vm.record(author_agent="a", change_type="patch", description="first")
    vm.mark_stable(r1.version_id, commit_sha="sha1")
    r2 = vm.record(author_agent="a", change_type="patch", description="second")
    vm.mark_stable(r2.version_id, commit_sha="sha2")
    latest = vm.last_stable()
    assert latest is not None
    assert latest.version_id == r2.version_id


def test_version_invalid_record_raises():
    from self_healing.version_manager import VersionManager
    vm = VersionManager()
    with pytest.raises((ValueError, TypeError)):
        vm.record()  # missing required fields


def test_version_unknown_mark_raises():
    from self_healing.version_manager import VersionManager
    vm = VersionManager()
    with pytest.raises(ValueError):
        vm.mark_stable("nonexistent-version-id")
