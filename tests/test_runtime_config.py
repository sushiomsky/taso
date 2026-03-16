"""
Tests for config/runtime_config.py.
"""
from __future__ import annotations

from config.runtime_config import RuntimeConfigManager


def test_feature_toggle_persists_to_env(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text("SWARM_ENABLED=true\n")

    mgr = RuntimeConfigManager(env_file)
    ok, detail = mgr.set_feature_enabled("swarm", False)
    assert ok is True
    assert detail == "SWARM_ENABLED"
    assert mgr.feature_status()["swarm"] is False
    assert "SWARM_ENABLED=false" in env_file.read_text()


def test_agent_toggle_rejects_protected_agent(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text("")

    mgr = RuntimeConfigManager(env_file)
    ok, msg = mgr.set_agent_enabled("coordinator", False)
    assert ok is False
    assert "cannot be disabled" in msg


def test_agent_toggle_disable_and_enable(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text("DISABLED_AGENTS=dev\n")

    mgr = RuntimeConfigManager(env_file)
    ok, detail = mgr.set_agent_enabled("research", False)
    assert ok is True
    assert detail == "research"
    assert "research" in mgr.disabled_agents()

    ok, detail = mgr.set_agent_enabled("research", True)
    assert ok is True
    assert "research" not in mgr.disabled_agents()


def test_model_backend_and_slot_updates(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text("")
    mgr = RuntimeConfigManager(env_file)

    ok, backend = mgr.set_backend("ollama")
    assert ok is True
    assert backend == "ollama"

    ok, key = mgr.set_model_slot("uncensored", "dolphin-mistral")
    assert ok is True
    assert key == "OLLAMA_UNCENSORED_MODEL"

    status = mgr.model_status()
    assert status["backend"] == "ollama"
    assert status["slots"]["uncensored"] == "dolphin-mistral"


def test_model_disable_and_enable(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text("DISABLED_MODELS=gpt-4o\n")
    mgr = RuntimeConfigManager(env_file)

    ok, name = mgr.set_model_enabled("openai/gpt-4o", False)
    assert ok is True
    assert "openai/gpt-4o" in mgr.disabled_models()

    ok, name = mgr.set_model_enabled("gpt-4o", True)
    assert ok is True
    disabled_lower = {m.lower() for m in mgr.disabled_models()}
    assert "gpt-4o" not in disabled_lower

