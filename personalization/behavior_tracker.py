"""
TASO – BehaviorTracker

Observes every user interaction, learns patterns, and updates UserProfiles:

  • Increments usage stats per intent/command
  • Detects response-style preference (concise vs detailed) from message length
  • Learns per-user NLP shortcuts when a phrase maps consistently to one intent
  • Promotes users to 'power_user' when activity thresholds are crossed
  • Triggers plugin auto-activation checks after every event
"""
from __future__ import annotations

import asyncio
from collections import defaultdict
from typing import Dict, List, Optional, TYPE_CHECKING

from config.logging_config import get_logger
from memory.user_profile_store import UserProfile, user_profile_store

if TYPE_CHECKING:
    from personalization.plugin_manager import PluginManager

log = get_logger("behavior_tracker")

# Interaction count at which a user is considered a power user
POWER_USER_THRESHOLD = 25
# Minimum times a phrase must map to the same intent before being added as a shortcut
SHORTCUT_MIN_CONSISTENT = 4
# Max custom shortcuts stored per user
MAX_SHORTCUTS = 30


class BehaviorTracker:
    """
    Sits in the message pipeline, records every interaction, and triggers
    profile updates. Designed to be non-blocking — all heavy work is
    dispatched to background tasks.
    """

    def __init__(self, profile_store=None) -> None:
        # Temporary in-memory phrase→intent observations before persisting
        # { user_id → { phrase → { intent → count } } }
        self._phrase_obs: Dict[int, Dict[str, Dict[str, int]]] = defaultdict(
            lambda: defaultdict(lambda: defaultdict(int))
        )
        self._plugin_manager: Optional["PluginManager"] = None
        # Allow injection of a custom profile store (e.g., for tests)
        if profile_store is not None:
            self._profile_store_override = profile_store
        else:
            self._profile_store_override = None

    def set_plugin_manager(self, pm: "PluginManager") -> None:
        self._plugin_manager = pm

    @property
    def _store(self):
        """Return the active profile store (injected or module-level singleton)."""
        if self._profile_store_override is not None:
            return self._profile_store_override
        from memory.user_profile_store import user_profile_store as _ups
        return _ups

    # ------------------------------------------------------------------
    # Public API — called from telegram_bot._handle_message
    # ------------------------------------------------------------------

    async def record(
        self,
        user_id: int,
        intent: str,
        raw_text: str,
        confidence: float,
        *,
        username: str = "",
        first_name: str = "",
    ) -> List[str]:
        """
        Record one interaction event. Returns a list of notification strings
        for new plugin unlocks (empty list if nothing new happened).
        """
        store = self._store
        # Ensure profile exists (cheap if already cached by get_or_create)
        profile = await store.get_or_create(user_id, username, first_name)

        # Log event — use create_task only when a loop is already running
        try:
            asyncio.get_running_loop()
            asyncio.create_task(store.log_event(user_id, "intent", intent))
        except RuntimeError:
            # No running loop (e.g., during sync tests) — skip fire-and-forget
            pass

        # Track phrase observations for shortcut learning
        self._observe_phrase(user_id, raw_text, intent, confidence)

        # Run profile updates in background — returns immediately
        notifications: List[str] = []
        try:
            notifications = await self._update_profile(profile, intent, raw_text)
        except Exception as exc:
            log.debug(f"Profile update error (non-fatal): {exc}")

        return notifications

    # ------------------------------------------------------------------
    # Profile update logic
    # ------------------------------------------------------------------

    async def _update_profile(
        self, profile: UserProfile, intent: str, raw_text: str
    ) -> List[str]:
        """Update the profile based on the new event. Returns unlock notifications."""
        notifications: List[str] = []
        changed = False

        # 1. Increment total interaction counter
        total = profile.total_interactions() + 1
        profile.metadata["total_interactions"] = total

        # 2. Detect and update response style preference
        new_style = self._infer_style(raw_text, profile)
        if new_style and new_style != profile.response_style:
            profile.response_style = new_style
            log.info(f"User {profile.user_id} style → {new_style}")
            changed = True

        # 3. Power user promotion
        if not profile.is_power_user() and total >= POWER_USER_THRESHOLD:
            profile.metadata["power_user"] = True
            notifications.append(
                "🏅 *You've been promoted to Power User!*\n"
                "Advanced commands and detailed responses are now active."
            )
            changed = True

        # 4. Auto-activate plugins based on usage
        if self._plugin_manager:
            stats = await self._store.get_stats(profile.user_id)
            new_plugins = self._plugin_manager.check_auto_activate(profile, stats)
            for plugin_id, plugin_name in new_plugins:
                if plugin_id not in profile.active_plugins:
                    profile.active_plugins.append(plugin_id)
                    notifications.append(
                        f"🔌 *Plugin unlocked: {plugin_name}*\n"
                        f"Your usage patterns activated `{plugin_id}`. "
                        f"Type _/plugins_ to see what's new."
                    )
                    changed = True

        # 5. Learn shortcuts from observed phrases
        shortcuts = self._extract_shortcuts(profile.user_id)
        if shortcuts:
            profile.learned_shortcuts.update(shortcuts)
            # Trim to max
            if len(profile.learned_shortcuts) > MAX_SHORTCUTS:
                # Keep most recently added
                keys = list(profile.learned_shortcuts.keys())
                for k in keys[:-MAX_SHORTCUTS]:
                    del profile.learned_shortcuts[k]
            changed = True

        if changed:
            await self._store.save(profile)

        return notifications

    # ------------------------------------------------------------------
    # Style inference
    # ------------------------------------------------------------------

    def _infer_style(self, text: str, profile: UserProfile) -> Optional[str]:
        """
        Infer response style from message length and vocabulary.
        Short terse messages → concise; long detailed messages → detailed;
        technical jargon → technical.
        """
        word_count = len(text.split())
        lower = text.lower()

        technical_keywords = {
            "cve", "exploit", "payload", "shellcode", "rop", "heap", "buffer",
            "overflow", "injection", "xss", "sqli", "lfi", "rce", "privesc",
            "lateral", "pivot", "c2", "exfil", "ioc", "ttps", "mitre", "siem",
        }
        if any(kw in lower for kw in technical_keywords):
            return "technical"
        if word_count <= 3:
            return "concise"
        if word_count >= 20:
            return "detailed"
        return None  # no change

    # ------------------------------------------------------------------
    # Shortcut learning
    # ------------------------------------------------------------------

    def _observe_phrase(
        self, user_id: int, raw_text: str, intent: str, confidence: float
    ) -> None:
        """Record a phrase→intent observation for shortcut learning."""
        if intent == "chat" or confidence < 0.75:
            return
        # Normalise: lowercase, strip punctuation, max 6 words
        words = raw_text.lower().strip("?.!").split()[:6]
        phrase = " ".join(words)
        if len(phrase) < 4:
            return
        self._phrase_obs[user_id][phrase][intent] += 1

    def _extract_shortcuts(self, user_id: int) -> Dict[str, str]:
        """
        Return phrases that consistently (SHORTCUT_MIN_CONSISTENT times)
        mapped to a single intent — these become personal shortcuts.
        """
        shortcuts: Dict[str, str] = {}
        obs = self._phrase_obs.get(user_id, {})
        for phrase, intent_counts in obs.items():
            if not intent_counts:
                continue
            top_intent = max(intent_counts, key=intent_counts.get)
            top_count = intent_counts[top_intent]
            total = sum(intent_counts.values())
            # Must be consistent (>80% of the time) and seen enough times
            if top_count >= SHORTCUT_MIN_CONSISTENT and top_count / total >= 0.8:
                shortcuts[phrase] = top_intent
        return shortcuts


# Module singleton
behavior_tracker = BehaviorTracker()
