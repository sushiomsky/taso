"""
TASO – Version Manager

Tracks every code/tool change with a unique version ID and metadata.
Integrates with Git tags and the version history database.
"""
from __future__ import annotations
import hashlib
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, List, Optional, Any

from config.logging_config import get_logger

log = get_logger("version_manager")


def make_version_id(prefix: str = "v") -> str:
    """Generate a short unique version ID: v20260314-a1b2c3d4"""
    ts = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    uid = uuid.uuid4().hex[:8]
    return f"{prefix}{ts}-{uid}"


@dataclass
class VersionRecord:
    version_id: str
    commit_sha: Optional[str]
    author_agent: str
    change_type: str        # "patch" | "tool_add" | "tool_update" | "agent_add" | "config"
    description: str
    files_changed: List[str] = field(default_factory=list)
    test_passed: bool = False
    deployed: bool = False
    stable: bool = False
    timestamp: float = field(default_factory=time.time)
    metadata: Dict[str, Any] = field(default_factory=dict)

    @property
    def datetime_str(self) -> str:
        return datetime.fromtimestamp(self.timestamp, tz=timezone.utc).isoformat()


class VersionManager:
    def __init__(self) -> None:
        self._records: Dict[str, VersionRecord] = {}
        self._stable_stack: List[str] = []  # ordered list of stable version IDs

    def record(self, **kwargs) -> VersionRecord:
        """Create and store a new version record."""
        try:
            if "version_id" not in kwargs:
                kwargs["version_id"] = make_version_id()
            rec = VersionRecord(**kwargs)
            self._records[rec.version_id] = rec
            log.info(f"VersionManager: recorded version {rec.version_id} ({rec.change_type} by {rec.author_agent})")
            return rec
        except TypeError as e:
            log.error(f"Invalid arguments provided for version record creation: {e}")
            raise ValueError("Invalid arguments provided for version record creation.") from e
        except Exception as e:
            log.error(f"Unexpected error while recording version: {e}")
            raise ValueError("Unexpected error occurred while creating version record.") from e

    def mark_stable(self, version_id: str, commit_sha: Optional[str] = None) -> None:
        """Mark a version as stable and optionally update its commit SHA."""
        try:
            rec = self._records.get(version_id)
            if not rec:
                log.error(f"VersionManager: Version ID {version_id} not found.")
                raise ValueError(f"Version ID {version_id} not found.")
            rec.stable = True
            rec.deployed = True
            if commit_sha:
                rec.commit_sha = commit_sha
            if version_id not in self._stable_stack:
                self._stable_stack.append(version_id)
            log.info(f"VersionManager: {version_id} marked as stable.")
        except ValueError as e:
            log.error(f"Error marking version {version_id} as stable: {e}")
            raise
        except Exception as e:
            log.error(f"Unexpected error while marking version {version_id} as stable: {e}")
            raise ValueError(f"Unexpected error occurred while marking version {version_id} as stable.") from e

    def last_stable(self) -> Optional[VersionRecord]:
        """Retrieve the most recent stable version record."""
        try:
            if not self._stable_stack:
                log.warning("VersionManager: No stable versions available.")
                return None
            return self._records.get(self._stable_stack[-1])
        except Exception as e:
            log.error(f"Unexpected error while retrieving last stable version: {e}")
            return None

    def prev_stable(self) -> Optional[VersionRecord]:
        """Retrieve the second most recent stable version record."""
        try:
            if len(self._stable_stack) < 2:
                log.warning("VersionManager: No previous stable version available.")
                return None
            return self._records.get(self._stable_stack[-2])
        except Exception as e:
            log.error(f"Unexpected error while retrieving previous stable version: {e}")
            return None

    def all_records(self, limit: int = 20) -> List[VersionRecord]:
        """Retrieve all version records, sorted by timestamp in descending order."""
        try:
            return sorted(self._records.values(), key=lambda r: r.timestamp, reverse=True)[:limit]
        except Exception as e:
            log.error(f"Failed to retrieve all records: {e}")
            return []

    def get(self, version_id: str) -> Optional[VersionRecord]:
        """Retrieve a specific version record by its ID."""
        try:
            return self._records.get(version_id)
        except Exception as e:
            log.error(f"Unexpected error while retrieving version {version_id}: {e}")
            return None

    def status_dict(self) -> Dict[str, Any]:
        """Generate a dictionary summarizing the current version status."""
        try:
            last = self.last_stable()
            return {
                "total_versions": len(self._records),
                "stable_versions": len(self._stable_stack),
                "last_stable": last.version_id if last else None,
                "last_stable_sha": last.commit_sha if last else None,
                "recent": [
                    {
                        "id": r.version_id,
                        "type": r.change_type,
                        "agent": r.author_agent,
                        "stable": r.stable,
                        "ts": r.datetime_str,
                    }
                    for r in self.all_records(5)
                ],
            }
        except Exception as e:
            log.error(f"Failed to generate status dictionary: {e}")
            return {
                "total_versions": 0,
                "stable_versions": 0,
                "last_stable": None,
                "last_stable_sha": None,
                "recent": [],
            }


version_manager = VersionManager()
