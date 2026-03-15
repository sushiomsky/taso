#!/bin/bash
# TASO pre-commit hook: run critical tests before every commit
set -e

cd "$(git rev-parse --show-toplevel)"

echo "=== TASO Pre-Commit Test Suite ==="
echo "Running bot command tests + self-healing tests..."

python3 -m pytest \
  tests/test_bot_commands.py \
  tests/test_git_self_healing.py \
  -q --tb=short 2>&1

EXIT_CODE=$?

if [ $EXIT_CODE -ne 0 ]; then
  echo ""
  echo "❌ Pre-commit tests FAILED. Commit blocked."
  echo "   Fix the failing tests before committing."
  exit 1
fi

echo ""
echo "✅ Pre-commit tests passed. Proceeding with commit."
exit 0
