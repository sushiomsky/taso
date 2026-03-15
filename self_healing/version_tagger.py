"""
TASO – Version Tagger

Semantic versioning (MAJOR.MINOR.PATCH) via Git tags.

Tag format: bot-vX.Y.Z
  PATCH  → bug fixes
  MINOR  → new features
  MAJOR  → breaking / architecture changes

Usage:
    from self_healing.version_tagger import version_tagger

    current = await version_tagger.get_current_version()  # "1.3.4"
    tag     = await version_tagger.tag_stable(bump="patch", message="fix: sandbox timeout")
    # → "bot-v1.3.5"

Per DEVELOPMENT_RULES.md §8 — all stable commits are tagged.
"""
from __future__ import annotations

import asyncio
import re
from typing import List, Optional, Tuple

from config.logging_config import get_logger
from self_healing.git_manager import _git as _run_git

log = get_logger("version_tagger")

_TAG_PATTERN = re.compile(r"^bot-v(\d+)\.(\d+)\.(\d+)$")
_DEFAULT_VERSION = (1, 0, 0)


class VersionTagger:
    """Create and manage semantic version tags on git commits."""

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    async def list_tags(self) -> List[str]:
        """Return all bot-v* tags sorted newest first."""
        ok, out, _ = await _run_git("tag", "--sort=-version:refname", "--list", "bot-v*")
        if not ok or not out.strip():
            return []
        return [t.strip() for t in out.strip().splitlines() if _TAG_PATTERN.match(t.strip())]

    async def get_current_version(self) -> str:
        """Return the latest tagged version string, e.g. '1.3.4'."""
        tags = await self.list_tags()
        if not tags:
            return ".".join(map(str, _DEFAULT_VERSION))
        m = _TAG_PATTERN.match(tags[0])
        if m:
            return f"{m.group(1)}.{m.group(2)}.{m.group(3)}"
        return ".".join(map(str, _DEFAULT_VERSION))

    async def get_latest_tag(self) -> Optional[str]:
        """Return the full tag name of the newest bot-v* tag."""
        tags = await self.list_tags()
        return tags[0] if tags else None

    async def parse_latest(self) -> Tuple[int, int, int]:
        """Parse latest tag into (major, minor, patch) ints."""
        tags = await self.list_tags()
        if not tags:
            return _DEFAULT_VERSION
        m = _TAG_PATTERN.match(tags[0])
        if m:
            return int(m.group(1)), int(m.group(2)), int(m.group(3))
        return _DEFAULT_VERSION

    # ------------------------------------------------------------------
    # Write
    # ------------------------------------------------------------------

    async def bump_patch(self, message: str = "") -> Optional[str]:
        """Increment PATCH, tag HEAD, return new tag."""
        maj, min_, patch = await self.parse_latest()
        return await self._create_tag(maj, min_, patch + 1, message)

    async def bump_minor(self, message: str = "") -> Optional[str]:
        """Increment MINOR, reset PATCH=0, tag HEAD."""
        maj, min_, _ = await self.parse_latest()
        return await self._create_tag(maj, min_ + 1, 0, message)

    async def bump_major(self, message: str = "") -> Optional[str]:
        """Increment MAJOR, reset MINOR=PATCH=0, tag HEAD."""
        maj, _, __ = await self.parse_latest()
        return await self._create_tag(maj + 1, 0, 0, message)

    async def tag_stable(
        self, bump: str = "patch", message: str = ""
    ) -> Optional[str]:
        """
        Tag the current HEAD as a stable release.

        Args:
            bump: 'patch' | 'minor' | 'major'
            message: annotation attached to the tag

        Returns the new tag name, e.g. 'bot-v1.3.5', or None on failure.
        """
        bump = bump.lower().strip()
        if bump == "major":
            tag = await self.bump_major(message)
        elif bump == "minor":
            tag = await self.bump_minor(message)
        else:
            tag = await self.bump_patch(message)

        if tag:
            log.info(f"VersionTagger: tagged HEAD as {tag}")
        else:
            log.warning("VersionTagger: tagging failed")
        return tag

    async def push_tags(self) -> bool:
        """Push all tags to origin."""
        ok, _, err = await _run_git("push", "origin", "--tags")
        if not ok:
            log.warning(f"VersionTagger: push tags failed: {err[:100]}")
        return ok

    # ------------------------------------------------------------------
    # Private
    # ------------------------------------------------------------------

    async def _create_tag(
        self, major: int, minor: int, patch: int, message: str
    ) -> Optional[str]:
        """Create an annotated git tag and push it."""
        tag = f"bot-v{major}.{minor}.{patch}"
        annotation = message or f"TASO stable release {tag}"

        # Create annotated tag
        ok, _, err = await _run_git("tag", "-a", tag, "-m", annotation)
        if not ok:
            if "already exists" in err:
                # Tag already exists — push it anyway
                log.warning(f"VersionTagger: tag {tag} already exists, skipping create")
            else:
                log.error(f"VersionTagger: failed to create tag {tag}: {err[:100]}")
                return None

        # Push tag to origin
        await self.push_tags()
        return tag


# Module-level singleton
version_tagger = VersionTagger()
