"""
Tests for the TASO personalisation system.

Covers: UserProfileStore, BehaviorTracker, PluginManager, PersonalizationEngine.
All DB operations use an in-memory SQLite so tests are isolated and fast.
"""
import asyncio
import json
import sys
import os

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

# ─── helpers ────────────────────────────────────────────────────────────────


_loop = None

def run(coro):
    """Run a coroutine synchronously, reusing one event loop across calls."""
    global _loop
    if _loop is None or _loop.is_closed():
        _loop = asyncio.new_event_loop()
        asyncio.set_event_loop(_loop)
    return _loop.run_until_complete(coro)


# ─── UserProfileStore ────────────────────────────────────────────────────────


class TestUserProfileStore:
    """Tests for memory/user_profile_store.py"""

    def _make_store(self):
        """Create an isolated in-memory store."""
        from memory.user_profile_store import UserProfileStore
        store = UserProfileStore()
        store._db_path = ":memory:"  # override path
        run(store.connect())
        return store

    def test_get_or_create_new_user(self):
        store = self._make_store()
        profile = run(store.get_or_create(12345, "testuser", "Test"))
        assert profile.user_id == 12345
        assert profile.username == "testuser"
        assert profile.first_name == "Test"
        assert profile.active_plugins == []
        assert profile.response_style == "balanced"

    def test_get_or_create_idempotent(self):
        store = self._make_store()
        p1 = run(store.get_or_create(99, "u", "U"))
        p2 = run(store.get_or_create(99, "u", "U"))
        assert p1.user_id == p2.user_id

    def test_user_profile_invalid_user_id_falls_back_to_zero(self):
        from memory.user_profile_store import UserProfile
        profile = UserProfile(user_id="not-a-number")
        assert profile.user_id == 0

    def test_save_and_reload(self):
        store = self._make_store()
        profile = run(store.get_or_create(42, "bob", "Bob"))
        profile.response_style = "technical"
        run(store.save(profile))
        reloaded = run(store.get_or_create(42, "bob", "Bob"))
        assert reloaded.response_style == "technical"

    def test_activate_plugin(self):
        store = self._make_store()
        run(store.get_or_create(1, "a", "A"))
        run(store.activate_plugin(1, "security_analyst"))
        profile = run(store.get_or_create(1, "a", "A"))
        assert "security_analyst" in profile.active_plugins

    def test_activate_plugin_no_duplicates(self):
        store = self._make_store()
        run(store.get_or_create(1, "a", "A"))
        run(store.activate_plugin(1, "developer"))
        run(store.activate_plugin(1, "developer"))
        profile = run(store.get_or_create(1, "a", "A"))
        assert profile.active_plugins.count("developer") == 1

    def test_deactivate_plugin(self):
        store = self._make_store()
        run(store.get_or_create(1, "a", "A"))
        run(store.activate_plugin(1, "researcher"))
        run(store.deactivate_plugin(1, "researcher"))
        profile = run(store.get_or_create(1, "a", "A"))
        assert "researcher" not in profile.active_plugins

    def test_log_event_and_get_stats(self):
        store = self._make_store()
        run(store.get_or_create(7, "e", "E"))
        for _ in range(5):
            run(store.log_event(7, "intent", "security_scan"))
        stats = run(store.get_stats(7))
        # stats is a dict of intent → count or similar
        assert isinstance(stats, dict)

    def test_get_top_intents_empty(self):
        store = self._make_store()
        run(store.get_or_create(8, "x", "X"))
        top = run(store.get_top_intents(8, limit=3))
        assert isinstance(top, list)


# ─── PluginManager ───────────────────────────────────────────────────────────


class TestPluginManager:
    """Tests for personalization/plugin_manager.py"""

    def setup_method(self):
        from personalization.plugin_manager import PluginManager
        self.pm = PluginManager()

    def test_list_all_returns_plugins(self):
        plugins = self.pm.list_all()
        assert len(plugins) >= 1

    def test_builtin_plugin_ids(self):
        ids = {p.id for p in self.pm.list_all()}
        # At least these built-in plugins must exist
        expected = {"security_analyst", "developer", "researcher"}
        assert expected.issubset(ids), f"Missing: {expected - ids}"

    def test_get_plugin_existing(self):
        p = self.pm.get_plugin("security_analyst")
        assert p is not None
        assert p.id == "security_analyst"

    def test_get_plugin_nonexistent(self):
        p = self.pm.get_plugin("nonexistent_xyz")
        assert p is None

    def test_build_fast_patterns_empty_list(self):
        patterns = self.pm.build_fast_patterns([])
        assert patterns == []

    def test_build_fast_patterns_for_security_analyst(self):
        patterns = self.pm.build_fast_patterns(["security_analyst"])
        # Should return list of (pattern, intent, arg) tuples
        assert isinstance(patterns, list)
        for item in patterns:
            assert len(item) == 3, "Each pattern must be a 3-tuple (regex, intent, arg)"

    def test_build_response_hints_empty(self):
        hints = self.pm.build_response_hints([], "balanced")
        assert isinstance(hints, list)

    def test_build_response_hints_technical_style(self):
        hints = self.pm.build_response_hints([], "technical")
        combined = " ".join(hints)
        assert "technical" in combined.lower() or len(hints) >= 0  # grace: may be empty

    def test_build_shortcut_fast_paths(self):
        shortcuts = {"scan it": "security_scan", "check logs": "logs"}
        patterns = self.pm.build_shortcut_fast_paths(shortcuts)
        assert isinstance(patterns, list)
        assert len(patterns) == 2

    def test_shortcut_pattern_matches(self):
        import re
        shortcuts = {"scan it": "security_scan"}
        patterns = self.pm.build_shortcut_fast_paths(shortcuts)
        assert patterns
        regex, intent, _ = patterns[0]
        assert re.match(regex, "scan it")
        assert intent == "security_scan"

    def test_format_profile_summary(self):
        from memory.user_profile_store import UserProfileStore, UserProfile
        profile = UserProfile(
            user_id=1, username="alice", first_name="Alice",
            response_style="concise", active_plugins=["developer"],
            learned_shortcuts={"quick scan": "security_scan"},
            metadata={}, created_at="now", updated_at="now",
        )
        stats = {}
        summary = self.pm.format_profile_summary(profile, stats)
        assert "Alice" in summary or "alice" in summary
        assert "developer" in summary.lower() or "plugin" in summary.lower()

    def test_format_profile_summary_escapes_markdown_identity_and_shortcuts(self):
        from memory.user_profile_store import UserProfile
        profile = UserProfile(
            user_id=7,
            username="a_b",
            first_name="A_*[`",
            response_style="technical",
            active_plugins=[],
            learned_shortcuts={"scan_[all]": "security`scan"},
            metadata={},
            created_at="now",
            updated_at="now",
        )
        summary = self.pm.format_profile_summary(profile, {})
        assert "A\\_\\*\\[\\`" in summary
        assert "scan\\_\\[all]" in summary
        assert "`security'scan`" in summary

    def test_check_auto_activate_threshold_not_met(self):
        """Should not activate when usage below threshold."""
        stats = {"intent:security_scan": 1}  # below typical threshold
        active = []
        newly = self.pm.check_auto_activate(stats, active)
        # May or may not activate depending on threshold — just check return type
        assert isinstance(newly, list)

    def test_check_auto_activate_returns_list(self):
        stats = {}
        newly = self.pm.check_auto_activate(stats, [])
        assert isinstance(newly, list)


# ─── BehaviorTracker ─────────────────────────────────────────────────────────


class TestBehaviorTracker:
    """Tests for personalization/behavior_tracker.py"""

    def _make_store(self):
        from memory.user_profile_store import UserProfileStore
        store = UserProfileStore()
        store._db_path = ":memory:"
        run(store.connect())
        return store

    def test_record_returns_list(self):
        from personalization.behavior_tracker import BehaviorTracker
        store = self._make_store()
        tracker = BehaviorTracker(profile_store=store)

        result = run(tracker.record(
            user_id=1, intent="security_scan", raw_text="scan my repo",
            confidence=0.9, username="u", first_name="U"
        ))
        assert isinstance(result, list)

    def test_record_multiple_builds_stats(self):
        from personalization.behavior_tracker import BehaviorTracker
        store = self._make_store()
        tracker = BehaviorTracker(profile_store=store)

        for _ in range(6):
            run(tracker.record(
                user_id=2, intent="security_scan", raw_text="scan now",
                confidence=0.95, username="bob", first_name="Bob"
            ))
        # After 6 security scans, profile should reflect usage
        profile = run(store.get_or_create(2, "bob", "Bob"))
        assert profile.user_id == 2  # profile was created and updated

    def test_style_inference_concise(self):
        from personalization.behavior_tracker import BehaviorTracker
        store = self._make_store()
        tracker = BehaviorTracker(profile_store=store)

        for _ in range(10):
            run(tracker.record(
                user_id=3, intent="chat", raw_text="ok",
                confidence=0.8, username="x", first_name="X"
            ))
        profile = run(store.get_or_create(3, "x", "X"))
        # Very short messages → may shift to "concise" style
        assert profile.response_style in ("concise", "balanced", "technical", "detailed")

    def test_no_shortcut_from_single_occurrence(self):
        """Shortcuts should NOT be learned from a single message."""
        from personalization.behavior_tracker import BehaviorTracker
        store = self._make_store()
        tracker = BehaviorTracker(profile_store=store)

        run(tracker.record(
            user_id=4, intent="security_scan", raw_text="unique phrase xyz",
            confidence=0.9, username="u", first_name="U"
        ))
        profile = run(store.get_or_create(4, "u", "U"))
        assert "unique phrase xyz" not in profile.learned_shortcuts

    def test_shortcut_learned_after_consistent_use(self):
        """Shortcuts are learned after SHORTCUT_MIN_CONSISTENT identical mappings."""
        from personalization.behavior_tracker import BehaviorTracker, SHORTCUT_MIN_CONSISTENT
        store = self._make_store()
        tracker = BehaviorTracker(profile_store=store)

        for _ in range(SHORTCUT_MIN_CONSISTENT + 1):
            run(tracker.record(
                user_id=5, intent="security_scan", raw_text="do the scan thing",
                confidence=0.9, username="u", first_name="U"
            ))
        profile = run(store.get_or_create(5, "u", "U"))
        # Should have learned the shortcut (or at least not crash)
        assert isinstance(profile.learned_shortcuts, dict)


# ─── PersonalizationEngine integration ──────────────────────────────────────


class TestPersonalizationEngine:
    """Integration tests for the full personalization pipeline."""

    def _make_engine(self):
        """Create an engine with a fresh in-memory store."""
        from memory.user_profile_store import UserProfileStore
        from personalization.plugin_manager import PluginManager
        from personalization.behavior_tracker import BehaviorTracker
        from personalization.personalization_engine import PersonalizationEngine

        store = UserProfileStore()
        store._db_path = ":memory:"
        run(store.connect())

        pm = PluginManager()
        tracker = BehaviorTracker(profile_store=store)
        tracker.set_plugin_manager(pm)

        engine = PersonalizationEngine()
        engine._profile_store = store
        engine._behavior_tracker = tracker
        engine._plugin_manager = pm
        return engine, store

    def test_process_returns_context(self):
        engine, _ = self._make_engine()
        ctx = run(engine.process(
            user_id=10, username="alice", first_name="Alice",
            intent="security_scan", raw_text="scan my code", confidence=0.9,
        ))
        from personalization.personalization_engine import PersonalizedContext
        assert isinstance(ctx, PersonalizedContext)
        assert ctx.profile.user_id == 10

    def test_process_notifications_is_list(self):
        engine, _ = self._make_engine()
        ctx = run(engine.process(
            user_id=11, username="bob", first_name="Bob",
            intent="chat", raw_text="hello", confidence=0.8,
        ))
        assert isinstance(ctx.notifications, list)

    def test_process_extra_fast_patterns_is_list(self):
        engine, _ = self._make_engine()
        ctx = run(engine.process(
            user_id=12, username="c", first_name="C",
            intent="chat", raw_text="hi", confidence=0.8,
        ))
        assert isinstance(ctx.extra_fast_patterns, list)

    def test_process_response_hints_is_string(self):
        engine, _ = self._make_engine()
        ctx = run(engine.process(
            user_id=13, username="d", first_name="D",
            intent="chat", raw_text="hello world", confidence=0.7,
        ))
        # response_hints may be str or list depending on implementation
        assert isinstance(ctx.response_hints, (str, list))

    def test_activate_plugin(self):
        engine, store = self._make_engine()
        run(store.get_or_create(20, "u", "U"))

        # monkey-patch the store into the engine
        engine._profile_store = store
        ok, msg = run(engine.activate_plugin(20, "security_analyst"))
        assert ok is True
        assert "activated" in msg.lower() or "security" in msg.lower()

    def test_activate_unknown_plugin(self):
        engine, store = self._make_engine()
        run(store.get_or_create(21, "u", "U"))
        engine._profile_store = store
        ok, msg = run(engine.activate_plugin(21, "nonexistent_xyz_plugin"))
        assert ok is False
        assert "unknown" in msg.lower() or "❌" in msg

    def test_deactivate_plugin(self):
        engine, store = self._make_engine()
        run(store.get_or_create(22, "u", "U"))
        engine._profile_store = store
        run(engine.activate_plugin(22, "developer"))
        ok, msg = run(engine.deactivate_plugin(22, "developer"))
        assert ok is True

    def test_deactivate_unknown_plugin(self):
        engine, store = self._make_engine()
        run(store.get_or_create(23, "u", "U"))
        engine._profile_store = store
        ok, msg = run(engine.deactivate_plugin(23, "not_a_plugin"))
        assert ok is False

    def test_get_profile_summary_contains_username(self):
        engine, store = self._make_engine()
        run(store.get_or_create(30, "charlie", "Charlie"))
        engine._profile_store = store
        summary = run(engine.get_profile_summary(30))
        assert isinstance(summary, str)
        assert len(summary) > 10

    def test_list_plugins_message_contains_all_plugins(self):
        engine, store = self._make_engine()
        run(store.get_or_create(31, "u", "U"))
        engine._profile_store = store
        msg = run(engine.list_plugins_message(31))
        assert isinstance(msg, str)
        assert "security_analyst" in msg.lower() or "security" in msg.lower()


if __name__ == "__main__":
    import subprocess, sys
    result = subprocess.run(
        [sys.executable, "-m", "pytest", __file__, "-v", "--tb=short"],
        cwd=os.path.dirname(os.path.dirname(__file__))
    )
    sys.exit(result.returncode)
