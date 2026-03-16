"""
TASO – PluginManager

Defines the built-in personalisation plugins and manages activation.

Each plugin is a dataclass with:
  - id, name, description
  - unlocked_tools: extra tool names made available
  - fast_patterns: user-specific NLP shortcuts added to the fast-path router
  - response_hints: text injected into the LLM system prompt when active
  - auto_activate_rules: {stat_key: min_count} — activate automatically when all met

Built-in plugins
----------------
security_analyst  – power security tools, CVE deep-dives, threat maps
developer         – full dev pipeline, patch tools, code review
researcher        – threat intel, OSINT, crawlers, memory search
sysadmin          – system monitoring, log analysis, resource alerts
power_user        – all-in-one: unlocked when total interactions >= 25
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple, TYPE_CHECKING

from config.logging_config import get_logger

if TYPE_CHECKING:
    from memory.user_profile_store import UserProfile

log = get_logger("plugin_manager")


@dataclass
class Plugin:
    id: str
    name: str
    description: str
    icon: str = "🔌"
    # Tool names made available when plugin is active
    unlocked_tools: List[str] = field(default_factory=list)
    # Extra fast-path patterns: (regex, intent, arg)
    fast_patterns: List[Tuple[str, str, str]] = field(default_factory=list)
    # Text appended to the NLP system prompt when plugin active
    response_hints: str = ""
    # Activation rules: {stat_key: min_count}
    # stat_key format: "intent:<name>" or "command:<name>"
    auto_activate_rules: Dict[str, int] = field(default_factory=dict)
    # Human-readable activation reason shown to user
    activation_message: str = ""


BUILTIN_PLUGINS: List[Plugin] = [
    Plugin(
        id="security_analyst",
        name="Security Analyst",
        icon="🛡️",
        description="Deep security analysis: CVE lookups, vulnerability scanning, threat maps, SIEM queries.",
        unlocked_tools=["dependency_scanner", "web_crawler", "repo_analyzer"],
        fast_patterns=[
            (r"^(cve|cve lookup|lookup cve)\s*(.*)$", "threat_intel", ""),
            (r"^(vuln scan|vulnerability scan|scan for vulns?)$", "security_scan", ""),
            (r"^(check deps?|dependency (scan|check))$", "security_scan", ""),
        ],
        response_hints=(
            "The user is a security analyst. Use CVE IDs, CVSS scores, and MITRE ATT&CK "
            "references where relevant. Be technically precise."
        ),
        auto_activate_rules={
            "intent:security_scan": 3,
            "intent:threat_intel": 2,
        },
        activation_message="Security scanning and threat intelligence tools are now enhanced.",
    ),
    Plugin(
        id="developer",
        name="Developer Mode",
        icon="⚙️",
        description="Full dev pipeline: feature creation, patch generation, code review, auto-deploy.",
        unlocked_tools=["git_manager", "sandbox_runner"],
        fast_patterns=[
            (r"^(build|create|implement|add) (.+)$", "add_feature", ""),
            (r"^(fix|patch|refactor) (.+)$", "dev_patch", ""),
            (r"^(review|check) (my )?code$", "code_audit", ""),
            (r"^(deploy|push|release)$", "dev_deploy", ""),
            (r"^(rollback|revert|undo)$", "dev_rollback", ""),
        ],
        response_hints=(
            "The user is a developer. Include code snippets, diffs, and technical details. "
            "Reference specific files and line numbers when known."
        ),
        auto_activate_rules={
            "intent:dev_task": 3,
            "intent:add_feature": 2,
        },
        activation_message="Developer tools activated. Full code pipeline now available.",
    ),
    Plugin(
        id="researcher",
        name="Researcher",
        icon="🔬",
        description="Threat intelligence, OSINT, CVE feeds, memory search, knowledge base queries.",
        unlocked_tools=["web_crawler"],
        fast_patterns=[
            (r"^(research|investigate|find info on|look up) (.+)$", "threat_intel", ""),
            (r"^(what do you know about|tell me about) (.+)$", "memory", ""),
            (r"^(learn|index|study) (.+)$", "learn_repo", ""),
        ],
        response_hints=(
            "The user is a researcher. Cite sources when possible, include references "
            "to CVEs, papers, or threat reports. Prefer depth over brevity."
        ),
        auto_activate_rules={
            "intent:threat_intel": 4,
            "intent:memory": 3,
        },
        activation_message="Research tools activated. Threat intel and knowledge search enhanced.",
    ),
    Plugin(
        id="sysadmin",
        name="Sysadmin",
        icon="🖥️",
        description="System monitoring, log analysis, resource alerts, process management.",
        unlocked_tools=["system_monitor", "log_analyzer"],
        fast_patterns=[
            (r"^(cpu|memory|ram|disk|resources?)$", "system", ""),
            (r"^(errors?|what went wrong|last errors?)$", "logs", ""),
            (r"^(health|healthcheck|is everything ok)$", "status", ""),
        ],
        response_hints=(
            "The user is a sysadmin. Include exact metric values (CPU%, RAM MB, disk %), "
            "process IDs, and log timestamps. Be terse and factual."
        ),
        auto_activate_rules={
            "intent:system": 4,
            "intent:logs": 3,
        },
        activation_message="Sysadmin mode activated. Monitoring and log tools enhanced.",
    ),
    Plugin(
        id="power_user",
        name="Power User",
        icon="🏅",
        description="All features unlocked. Detailed responses, all tools, swarm access.",
        unlocked_tools=[],  # everything is already available
        fast_patterns=[],
        response_hints=(
            "The user is an experienced power user. Skip beginner explanations, "
            "go straight to technical details, and include all relevant options."
        ),
        auto_activate_rules={},  # activated by BehaviorTracker on interaction count
        activation_message="All features and tools are now available.",
    ),
]

# Index by id
_PLUGIN_INDEX: Dict[str, Plugin] = {p.id: p for p in BUILTIN_PLUGINS}


class PluginManager:
    """Manages the plugin registry and per-user activation checks."""

    def get_plugin(self, plugin_id: str) -> Optional[Plugin]:
        return _PLUGIN_INDEX.get(plugin_id)

    def list_all(self) -> List[Plugin]:
        return list(BUILTIN_PLUGINS)

    def get_active(self, active_plugin_ids: List[str]) -> List[Plugin]:
        return [_PLUGIN_INDEX[pid] for pid in active_plugin_ids if pid in _PLUGIN_INDEX]

    # ------------------------------------------------------------------
    # Auto-activation
    # ------------------------------------------------------------------

    def check_auto_activate(
        self,
        profile_or_stats: "UserProfile" | Dict[str, int],
        stats_or_active: Dict[str, int] | List[str] | None = None,
    ) -> List[Tuple[str, str]]:
        """
        Check all plugins for auto-activation candidates.

        Supports two call styles for backward compatibility:
        1) check_auto_activate(profile, stats) -> current flow
        2) check_auto_activate(stats, active_plugin_ids) -> legacy/tests
        """
        newly_activated: List[Tuple[str, str]] = []
        if isinstance(profile_or_stats, dict):
            profile = None
            stats = profile_or_stats
            active_plugins = list(stats_or_active or [])
        else:
            profile = profile_or_stats
            stats = stats_or_active if isinstance(stats_or_active, dict) else {}
            active_plugins = profile.active_plugins

        for plugin in BUILTIN_PLUGINS:
            if plugin.id in active_plugins:
                continue  # already active
            if not plugin.auto_activate_rules:
                continue  # manual-only (power_user is handled by BehaviorTracker)
            if all(stats.get(k, 0) >= v for k, v in plugin.auto_activate_rules.items()):
                newly_activated.append((plugin.id, plugin.name))
                if profile is not None:
                    log.info(f"Auto-activating plugin '{plugin.id}' for user {profile.user_id}")
                else:
                    log.info(f"Auto-activating plugin '{plugin.id}'")
        return newly_activated

    # ------------------------------------------------------------------
    # Personalised NLP context
    # ------------------------------------------------------------------

    def build_fast_patterns(self, active_plugin_ids: List[str]) -> List[tuple]:
        """Aggregate fast-path patterns from all active plugins."""
        patterns: List[tuple] = []
        for plugin in self.get_active(active_plugin_ids):
            patterns.extend(plugin.fast_patterns)
        return patterns

    def build_response_hints(self, active_plugin_ids: List[str], style: str) -> List[str]:
        """Build system-prompt hint lines from active plugins + response style."""
        hints: List[str] = []

        style_hints = {
            "concise":   "Keep responses brief — bullets and short sentences only.",
            "detailed":  "Provide thorough explanations with context and examples.",
            "technical": "Use precise technical language; include code/commands where helpful.",
            "balanced":  "",
        }
        style_hint = style_hints.get(style, "")
        if style_hint:
            hints.append(style_hint)

        for plugin in self.get_active(active_plugin_ids):
            if plugin.response_hints:
                hints.append(plugin.response_hints)

        return hints

    def build_shortcut_fast_paths(
        self, learned_shortcuts: Dict[str, str]
    ) -> List[tuple]:
        """Convert learned shortcuts into fast-path tuples."""
        patterns = []
        for phrase, intent in learned_shortcuts.items():
            escaped = re.escape(phrase)
            patterns.append((f"^{escaped}$", intent, ""))
        return patterns

    def format_profile_summary(self, profile: "UserProfile", stats: Dict[str, int]) -> str:
        """Format a user-facing profile summary."""
        active = self.get_active(profile.active_plugins)
        plugin_lines = "\n".join(
            f"  {p.icon} *{p.name}* — {p.description}" for p in active
        ) or "  _None yet — keep using TASO to unlock plugins!_"

        top_intents = sorted(
            [(k[len("intent:"):], v) for k, v in stats.items() if k.startswith("intent:")],
            key=lambda x: x[1], reverse=True,
        )[:5]
        usage_lines = "\n".join(
            f"  • `{intent}` × {count}" for intent, count in top_intents
        ) or "  _No data yet_"

        shortcuts = profile.learned_shortcuts
        shortcut_lines = "\n".join(
            f"  • \"{phrase}\" → `{intent}`"
            for phrase, intent in list(shortcuts.items())[:8]
        ) or "  _None learned yet_"

        style_label = {
            "concise": "Concise 📝",
            "detailed": "Detailed 📖",
            "technical": "Technical 🔬",
            "balanced": "Balanced ⚖️",
        }.get(profile.response_style, profile.response_style)

        power = "🏅 Power User" if profile.is_power_user() else f"({profile.total_interactions()} interactions)"
        identity = profile.first_name or profile.username or f"user {profile.user_id}"

        return (
            f"👤 *Your TASO Profile* — {identity} {power}\n\n"
            f"🎨 *Response style:* {style_label}\n\n"
            f"🔌 *Active plugins ({len(active)}):*\n{plugin_lines}\n\n"
            f"📊 *Top commands:*\n{usage_lines}\n\n"
            f"⚡ *Learned shortcuts:*\n{shortcut_lines}"
        )


# Module singleton
plugin_manager = PluginManager()
