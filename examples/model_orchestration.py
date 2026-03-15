#!/usr/bin/env python3
"""
TASO Example: Multi-Model Orchestration
=========================================
Shows how ModelRouter selects and routes tasks to the appropriate
model, with fallback chain: primary → alternative → uncensored.

Run with:
    cd /root/taso && python examples/model_orchestration.py
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


async def demo_task_routing():
    """Show how tasks are routed to different models by type."""
    from models.model_registry import ModelRegistry, TaskType
    from models.model_router import ModelRouter, classify_task

    registry = ModelRegistry()
    router   = ModelRouter(reg=registry)

    test_prompts = [
        # (prompt, expected_type, description)
        ("Write a Python function to parse JSON",               TaskType.CODING,    "coding"),
        ("Find security vulnerabilities in this code",          TaskType.SECURITY,  "security"),
        ("What are the latest CVEs for OpenSSL?",               TaskType.SECURITY,  "security/research"),
        ("Analyse the security posture of this codebase",       TaskType.SECURITY,  "security"),
        ("Hello, how are you?",                                 TaskType.GENERAL,   "general"),
    ]

    print("\n" + "="*60)
    print("TASK TYPE CLASSIFICATION")
    print("="*60)
    for prompt, expected, label in test_prompts:
        detected = classify_task(prompt)
        match = "✅" if detected == expected else "ℹ️ "
        print(f"  {match}  [{detected.value:10s}]  ({label:18s})  {prompt[:45]}…")

    print("\n" + "="*60)
    print("MODEL REGISTRY")
    print("="*60)
    for model in registry.all_models():
        caps  = ", ".join(model.capabilities[:3])
        uncen = "🔓" if getattr(model, "uncensored", False) else "  "
        print(f"  {uncen} {model.name:30s}  provider={model.provider:12s}  caps=[{caps}]")

    print("\n" + "="*60)
    print("PREFERRED MODEL PER TASK TYPE")
    print("="*60)
    for task_type in TaskType:
        preferred = registry.preferred_for(task_type)
        if preferred:
            print(f"  {task_type.value:12s} → {preferred.name}")

    uncensored = registry.uncensored_model()
    print(f"\n  Uncensored fallback: {uncensored.name if uncensored else 'none'}")


async def demo_refusal_fallback():
    """Show the refusal detection and fallback chain."""
    from models.ollama_client import is_refusal

    refusals = [
        "I cannot assist with that request.",
        "I'm sorry, but I'm unable to help with that task.",
        "As an AI, I'm not able to provide that information.",
        "I must decline to assist with this request.",
    ]
    normal_responses = [
        "Here is the Python code you requested:\n\n```python\ndef hello(): pass\n```",
        "The vulnerability exists in line 42 of auth.py where the password is not hashed.",
        "Based on my analysis, the top 3 CVEs affecting this dependency are…",
    ]

    print("\n" + "="*60)
    print("REFUSAL DETECTION")
    print("="*60)
    print("  Refusals (should be True):")
    for r in refusals:
        detected = is_refusal(r)
        print(f"    {'✅' if detected else '❌'}  {r[:60]}")

    print("  Normal responses (should be False):")
    for r in normal_responses:
        detected = is_refusal(r)
        print(f"    {'✅' if not detected else '❌'}  {r[:60]}")


async def demo_ollama_status():
    """Check Ollama availability and list models."""
    from models.ollama_client import OllamaClient

    print("\n" + "="*60)
    print("OLLAMA STATUS")
    print("="*60)

    client = OllamaClient()
    healthy = await client.health()
    print(f"  Server reachable: {'✅' if healthy else '❌'}")

    if healthy:
        models = await client.list_models()
        print(f"  Available models: {models if models else '(none pulled yet)'}")


if __name__ == "__main__":
    print("TASO Multi-Model Orchestration Demo\n")
    asyncio.run(demo_task_routing())
    asyncio.run(demo_refusal_fallback())
    asyncio.run(demo_ollama_status())
