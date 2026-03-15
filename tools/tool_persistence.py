"""
TASO – Tool Persistence

Saves generated dynamic tools to disk as JSON records and reloads them
on startup, so the tool registry survives bot restarts.

Storage location: data/dynamic_tools/<name>.json

Each record contains:
  name, code, description, input_schema, output_schema, tags, version,
  created_at (ISO timestamp), author_agent

Consumed by:
  - ToolRegistry.register_dynamic()  → auto-persists on registration
  - orchestrator._initialize_tools() → loads all persisted tools at startup
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

from config.logging_config import get_logger

log = get_logger("tool_generator")

_PERSIST_DIR = Path(__file__).resolve().parent.parent / "data" / "dynamic_tools"


def _persist_dir() -> Path:
    """Return (and create) the dynamic tools persist directory."""
    _PERSIST_DIR.mkdir(parents=True, exist_ok=True)
    return _PERSIST_DIR


def persist_tool(
    name: str,
    code: str,
    description: str,
    input_schema: Dict[str, Any] = None,
    output_schema: Dict[str, Any] = None,
    tags: List[str] = None,
    version: str = "1.0.0",
    author_agent: str = "developer",
) -> bool:
    """
    Write a dynamic tool record to disk.
    Returns True on success, False on error.
    """
    record = {
        "name": name,
        "code": code,
        "description": description,
        "input_schema": input_schema or {},
        "output_schema": output_schema or {},
        "tags": tags or ["dynamic"],
        "version": version,
        "author_agent": author_agent,
        "created_at": datetime.now(tz=timezone.utc).isoformat(),
    }
    target = _persist_dir() / f"{name}.json"
    try:
        target.write_text(json.dumps(record, indent=2), encoding="utf-8")
        log.debug(f"ToolPersistence: saved '{name}' → {target}")
        return True
    except Exception as exc:
        log.error(f"ToolPersistence: failed to save '{name}': {exc}")
        return False


def load_all() -> List[Dict[str, Any]]:
    """
    Return a list of all persisted tool records (dicts).
    Does not register them — caller must call registry.register_dynamic() for each.
    """
    d = _persist_dir()
    records: List[Dict[str, Any]] = []
    for path in sorted(d.glob("*.json")):
        try:
            records.append(json.loads(path.read_text(encoding="utf-8")))
        except Exception as exc:
            log.error(f"ToolPersistence: failed to load '{path.name}': {exc}")
    return records


def list_persisted() -> List[str]:
    """Return names of all persisted dynamic tools."""
    return [p.stem for p in sorted(_persist_dir().glob("*.json"))]


def delete_persisted(name: str) -> bool:
    """Remove a persisted tool record from disk. Returns True if deleted."""
    target = _persist_dir() / f"{name}.json"
    if target.exists():
        target.unlink()
        log.info(f"ToolPersistence: deleted '{name}'")
        return True
    return False
