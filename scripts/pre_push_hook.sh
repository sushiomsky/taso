#!/bin/bash
# TASO pre-push hook: run runtime smoke tests before pushing to remote
# Blocks push if critical runtime checks fail

set -euo pipefail
cd "$(git rev-parse --show-toplevel)"

echo "=== TASO Pre-Push Runtime Tests ==="

# 1. Syntax check all Python files
echo "▶ Syntax check..."
python3 -m py_compile bot/telegram_bot.py orchestrator.py main.py agents/coordinator_agent.py
echo "  ✓ Syntax OK"

# 2. Run bot command + self-healing unit tests
echo "▶ Unit tests (bot commands + self-healing)..."
python3 -m pytest tests/test_bot_commands.py tests/test_git_self_healing.py -q --tb=short 2>&1
echo "  ✓ Unit tests passed"

# 3. Runtime import smoke test (does the full app import without error?)
echo "▶ Import smoke test..."
python3 -c "
import sys, asyncio
sys.path.insert(0, '.')
try:
    from config.settings import settings
    from agents.coordinator_agent import CoordinatorAgent
    from agents.message_bus import MessageBus
    from tools.base_tool import tool_registry
    from memory.knowledge_db import KnowledgeDB
    from bot.telegram_bot import TelegramBot
    print('  ✓ All critical imports OK')
except Exception as e:
    print(f'  ✗ Import failed: {e}')
    sys.exit(1)
" 2>&1

# 4. Check for _tasks conflict (the coordinator shadow bug)
echo "▶ Checking for _tasks conflict in agents..."
python3 -c "
import ast, sys, pathlib
issues = []
for f in pathlib.Path('agents').glob('*.py'):
    try:
        tree = ast.parse(f.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                for item in ast.walk(node):
                    if isinstance(item, ast.Assign):
                        for t in item.targets:
                            if isinstance(t, ast.Attribute) and t.attr == '_tasks':
                                issues.append(f'{f.name}:{item.lineno} redefines _tasks')
    except:
        pass
if issues:
    print('  ✗ _tasks conflict detected:')
    for i in issues: print(f'    {i}')
    sys.exit(1)
print('  ✓ No _tasks conflicts')
" 2>&1

echo ""
echo "✅ All pre-push checks passed. Pushing..."
exit 0
