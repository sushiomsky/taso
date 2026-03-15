"""
TASO – PersonalizationEngine

The single entry point for all personalisation logic.
Called once per message; returns a PersonalizedContext used by the bot.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, TYPE_CHECKING

from config.logging_config import get_logger
from memory.user_profile_store import UserProfile, user_profile_store
from personalization.behavior_tracker import behavior_tracker
from personalization.plugin_manager import plugin_manager

if TYPE_CHECKING:
    pass

log = get_logger("personalization_engine")

# Wire plugin_manager into behavior_tracker so auto-activation works
behavior_tracker.set_plugin_manager(plugin_manager)


@dataclass
class PersonalizedContext:
    """Everything the bot needs to personalise one message interaction."""
    profile: UserProfile
    # Combined fast-path patterns: global + plugin + learned shortcuts
    extra_fast_patterns: List[tuple] = field(default_factory=list)
    # Extra text to append to LLM system prompts
    response_hints: str = ""
    # Notifications to deliver to the user after the main response
    notifications: List[str] = field(default_factory=list)


class PersonalizationEngine:
    """Orchestrates profile loading, behaviour tracking, and context assembly."""

    def __init__(self):
        # Allow tests to inject alternative implementations
        self._profile_store    = user_profile_store
        self._behavior_tracker = behavior_tracker
        self._plugin_manager   = plugin_manager

    async def process(
        self,
        user_id: int,
        username: str,
        first_name: str,
        intent: str,
        raw_text: str,
        confidence: float,
    ) -> PersonalizedContext:
        """
        Called after intent classification. Returns a PersonalizedContext
        with everything needed to personalise the current response.
        """
        ps  = self._profile_store
        bt  = self._behavior_tracker
        pm_ = self._plugin_manager

        # 1. Record the interaction and get any unlock notifications
        notifications = await bt.record(
            user_id=user_id,
            intent=intent,
            raw_text=raw_text,
            confidence=confidence,
            username=username,
            first_name=first_name,
        )

        # 2. Reload profile (behavior_tracker may have updated it)
        profile = await ps.get_or_create(user_id, username, first_name)

        # 3. Build extra fast patterns from plugins + learned shortcuts
        plugin_patterns   = pm_.build_fast_patterns(profile.active_plugins)
        shortcut_patterns = pm_.build_shortcut_fast_paths(profile.learned_shortcuts)
        extra_patterns    = shortcut_patterns + plugin_patterns  # shortcuts take precedence

        # 4. Build response style hints
        response_hints = pm_.build_response_hints(
            profile.active_plugins, profile.response_style
        )

        return PersonalizedContext(
            profile=profile,
            extra_fast_patterns=extra_patterns,
            response_hints=response_hints,
            notifications=notifications,
        )

    async def get_profile_summary(self, user_id: int) -> str:
        """Return a formatted profile summary string for /profile command."""
        profile = await self._profile_store.get_or_create(user_id)
        stats   = await self._profile_store.get_stats(user_id)
        return self._plugin_manager.format_profile_summary(profile, stats)

    async def activate_plugin(self, user_id: int, plugin_id: str) -> tuple[bool, str]:
        """
        Manually activate a plugin for a user.
        Returns (success, message).
        """
        plugin = self._plugin_manager.get_plugin(plugin_id)
        if not plugin:
            available = ", ".join(p.id for p in self._plugin_manager.list_all())
            return False, f"❌ Unknown plugin `{plugin_id}`.\nAvailable: {available}"
        await self._profile_store.activate_plugin(user_id, plugin_id)
        return True, (
            f"{plugin.icon} *{plugin.name}* activated!\n\n"
            f"{plugin.activation_message}"
        )

    async def deactivate_plugin(self, user_id: int, plugin_id: str) -> tuple[bool, str]:
        """Manually deactivate a plugin. Returns (success, message)."""
        plugin = self._plugin_manager.get_plugin(plugin_id)
        if not plugin:
            return False, f"❌ Unknown plugin `{plugin_id}`."
        await self._profile_store.deactivate_plugin(user_id, plugin_id)
        return True, f"🔌 *{plugin.name}* deactivated."

    async def list_plugins_message(self, user_id: int) -> str:
        """Format the full plugin catalogue with activation status for this user."""
        profile = await self._profile_store.get_or_create(user_id)
        active_ids = profile.active_plugins
        pm_ = self._plugin_manager
        lines = ["🔌 *Available Plugins*\n"]
        for p in pm_.list_all():
            status = "✅ Active" if p.id in active_ids else "⬜ Inactive"
            rules = ""
            if p.auto_activate_rules:
                rule_parts = [f"{k.split(':')[1]} × {v}" for k, v in p.auto_activate_rules.items()]
                rules = f"\n   _Auto-unlocks at: {', '.join(rule_parts)}_"
            lines.append(
                f"{p.icon} *{p.name}* — {status}\n"
                f"   {p.description}{rules}"
            )
        lines.append(
            "\n_Use_ `/activate <plugin_id>` _to enable manually._\n"
            "_Plugin IDs:_ " + " | ".join(f"`{p.id}`" for p in pm_.list_all())
        )
        return "\n\n".join(lines[1:]).strip()


# Module singleton
personalization_engine = PersonalizationEngine()
