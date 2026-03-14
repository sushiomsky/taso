#!/usr/bin/env bash
# ============================================================
# TASO – Install Script
# Installs all system and Python dependencies for local
# (non-Docker) deployment.
# ============================================================
set -euo pipefail

PYTHON=${PYTHON:-python3.11}
VENV_DIR="${VENV_DIR:-.venv}"

echo "=================================================="
echo " TASO – Telegram Autonomous Security Operator"
echo " Installation Script"
echo "=================================================="

# -- Python version check
if ! command -v "$PYTHON" &>/dev/null; then
    echo "ERROR: $PYTHON not found. Install Python 3.11+."
    exit 1
fi

PY_VER=$("$PYTHON" -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
echo "Python version: $PY_VER"

# -- Create virtualenv
if [ ! -d "$VENV_DIR" ]; then
    echo "Creating virtual environment in $VENV_DIR ..."
    "$PYTHON" -m venv "$VENV_DIR"
fi

source "$VENV_DIR/bin/activate"

echo "Upgrading pip..."
pip install --quiet --upgrade pip wheel setuptools

echo "Installing Python dependencies..."
pip install --quiet -r requirements.txt

# -- .env setup
if [ ! -f ".env" ]; then
    echo ""
    echo "Creating .env from .env.example ..."
    cp .env.example .env
    echo ">>> IMPORTANT: Edit .env and set your TELEGRAM_BOT_TOKEN and admin IDs."
fi

# -- Data directories
mkdir -p data logs

# -- Check Docker
if command -v docker &>/dev/null; then
    echo "Docker: found ($(docker --version))"
    echo "Pulling sandbox image..."
    docker pull python:3.11-slim --quiet || true
else
    echo "WARNING: Docker not found. Sandbox execution will be unavailable."
fi

# -- Check Ollama (optional)
if command -v ollama &>/dev/null; then
    echo "Ollama: found"
else
    echo "INFO: Ollama not found. Install from https://ollama.ai for local LLM support."
    echo "      Alternatively set LLM_BACKEND=openai or LLM_BACKEND=anthropic in .env"
fi

# -- Check git
if ! command -v git &>/dev/null; then
    echo "WARNING: git not found. Git operations will be unavailable."
fi

echo ""
echo "=================================================="
echo " Installation complete!"
echo ""
echo " Next steps:"
echo "   1. Edit .env with your Telegram bot token"
echo "   2. Set your Telegram admin user IDs"
echo "   3. Configure your LLM backend"
echo "   4. Run:  source $VENV_DIR/bin/activate && python main.py"
echo "=================================================="
