#!/bin/bash
# TASO Smart Watchdog
# Monitors the bot process, classifies crashes, and only restarts after diagnosis.
# Does NOT blindly loop — same crash type gets max 3 attempts before giving up.

set -euo pipefail

TASO_DIR="/root/taso"
LOG="$TASO_DIR/logs/watchdog.log"
CRASH_LOG="$TASO_DIR/logs/crash_analysis.log"
BOT_CMD="python3 main.py"
MAX_SAME_CRASH=3    # max restarts for identical crash type before giving up
MIN_UPTIME=30       # seconds bot must run to reset crash counter
BACKOFF=10          # base backoff seconds between restarts

mkdir -p "$TASO_DIR/logs"

_log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" | tee -a "$LOG"; }
_err() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] ERROR: $*" | tee -a "$LOG" >&2; }

# Send Telegram notification
_notify() {
    local msg="$1"
    python3 - <<PYEOF 2>/dev/null || true
import asyncio, sys
sys.path.insert(0, "$TASO_DIR")
import os; os.chdir("$TASO_DIR")
from config.settings import settings
import httpx

async def send():
    token = settings.TELEGRAM_BOT_TOKEN
    chat_id = settings.TELEGRAM_ADMIN_CHAT_ID or (settings.TELEGRAM_ADMIN_IDS[0] if settings.TELEGRAM_ADMIN_IDS else None)
    if not token or not chat_id:
        return
    async with httpx.AsyncClient(timeout=10) as c:
        await c.post(f"https://api.telegram.org/bot{token}/sendMessage",
                     json={"chat_id": chat_id, "text": "$msg", "parse_mode": "Markdown"})
asyncio.run(send())
PYEOF
}

# Classify crash from exit code + recent log tail
_classify_crash() {
    local exit_code="$1"
    local log_tail="$2"

    # OOM kill
    if [ "$exit_code" = "-9" ] || [ "$exit_code" = "137" ]; then
        echo "OOM_KILL"
        return
    fi

    # Telegram conflict (another instance running)
    if echo "$log_tail" | grep -qi "conflict\|409\|terminated by other getUpdates"; then
        echo "TELEGRAM_CONFLICT"
        return
    fi

    # Clean exit (intentional shutdown)
    if [ "$exit_code" = "0" ]; then
        echo "CLEAN_EXIT"
        return
    fi

    # Import / syntax error
    if echo "$log_tail" | grep -qi "ImportError\|ModuleNotFoundError\|SyntaxError"; then
        echo "IMPORT_ERROR"
        return
    fi

    # Network error
    if echo "$log_tail" | grep -qi "NetworkError\|ConnectionRefused\|TimeoutError"; then
        echo "NETWORK_ERROR"
        return
    fi

    # Generic Python exception
    if echo "$log_tail" | grep -qi "Traceback\|Exception\|Error"; then
        echo "PYTHON_ERROR"
        return
    fi

    echo "UNKNOWN_EXIT_$exit_code"
}

# Attempt to fix based on crash type
_attempt_fix() {
    local crash_type="$1"
    local log_tail="$2"

    case "$crash_type" in
        OOM_KILL)
            _log "OOM detected. Clearing Python cache and dropping caches..."
            find "$TASO_DIR" -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
            sync; echo 3 > /proc/sys/vm/drop_caches 2>/dev/null || true
            sleep 5
            _log "Memory after cleanup: $(free -h | grep Mem)"
            ;;
        TELEGRAM_CONFLICT)
            _log "Telegram conflict: killing other bot instances..."
            pgrep -f "python3 main.py" | while read pid; do
                _log "  Killing PID $pid"
                kill "$pid" 2>/dev/null || true
            done
            sleep 10  # Wait for Telegram to release the polling slot
            ;;
        IMPORT_ERROR)
            _log "Import error detected. Running syntax check..."
            cd "$TASO_DIR"
            python3 -m py_compile bot/telegram_bot.py orchestrator.py main.py 2>&1 | tee -a "$CRASH_LOG"
            _log "Re-installing dependencies..."
            TMPDIR=/root/tmp pip install -q --no-cache-dir --break-system-packages -r requirements.txt 2>&1 | tail -5 | tee -a "$CRASH_LOG" || true
            ;;
        NETWORK_ERROR)
            _log "Network error. Waiting 30s for connectivity..."
            sleep 30
            ;;
        CLEAN_EXIT)
            _log "Clean exit — not restarting (intentional shutdown)."
            return 1  # Signal: do NOT restart
            ;;
        *)
            _log "Unknown crash ($crash_type). Waiting before retry..."
            sleep "$BACKOFF"
            ;;
    esac
    return 0  # OK to restart
}

# Main watchdog loop
main() {
    _log "=== TASO Watchdog started (PID $$) ==="
    _log "Bot command: $BOT_CMD | Max same-crash retries: $MAX_SAME_CRASH"
    cd "$TASO_DIR"

    declare -A crash_counts
    last_crash_type=""

    while true; do
        # Ensure only one bot instance before starting
        local existing
        existing=$(pgrep -f "python3 main.py" 2>/dev/null | head -1 || true)
        if [ -n "$existing" ]; then
            _log "Another bot instance (PID $existing) running — killing it first"
            kill "$existing" 2>/dev/null || true
            sleep 3
        fi

        _log "Starting bot..."
        start_time=$(date +%s)

        # Run the bot; capture its PID and exit code
        $BOT_CMD >> "$TASO_DIR/logs/bot.log" 2>&1 &
        BOT_PID=$!
        _log "Bot started PID $BOT_PID"

        # Wait for it to exit
        wait "$BOT_PID" 2>/dev/null
        exit_code=$?
        end_time=$(date +%s)
        uptime=$((end_time - start_time))

        _log "Bot exited (PID $BOT_PID) code=$exit_code uptime=${uptime}s"

        # Get last 50 lines of logs for diagnosis
        log_tail=$(tail -50 "$TASO_DIR/logs/combined.log" 2>/dev/null || tail -50 "$TASO_DIR/logs/bot.log" 2>/dev/null || echo "")

        crash_type=$(_classify_crash "$exit_code" "$log_tail")
        _log "Crash classification: $crash_type"
        echo "[$(date '+%Y-%m-%d %H:%M:%S')] type=$crash_type exit=$exit_code uptime=${uptime}s" >> "$CRASH_LOG"

        # Clean exit — do not restart
        if [ "$crash_type" = "CLEAN_EXIT" ]; then
            _log "Bot stopped cleanly — watchdog exiting."
            _notify "🛑 TASO stopped cleanly (clean exit)."
            break
        fi

        # Reset crash counter if bot ran long enough
        if [ "$uptime" -ge "$MIN_UPTIME" ]; then
            _log "Uptime $uptime >= $MIN_UPTIME — resetting crash counters"
            unset crash_counts
            declare -A crash_counts
        fi

        # Increment crash counter for this type
        crash_counts[$crash_type]=$(( ${crash_counts[$crash_type]:-0} + 1 ))
        count=${crash_counts[$crash_type]}

        _log "Crash type '$crash_type' count: $count/$MAX_SAME_CRASH"

        if [ "$count" -ge "$MAX_SAME_CRASH" ]; then
            _err "Too many '$crash_type' crashes ($count). Giving up. Manual intervention needed."
            _notify "🚨 TASO watchdog GIVING UP after $count '$crash_type' crashes. Manual fix needed.\n\nLast log:\n\`\`\`$(echo "$log_tail" | tail -10)\`\`\`"
            exit 1
        fi

        # Attempt automated fix
        if ! _attempt_fix "$crash_type" "$log_tail"; then
            _log "Fix function declined restart for $crash_type"
            break
        fi

        # Backoff before restart (exponential)
        wait_time=$(( BACKOFF * count ))
        _log "Waiting ${wait_time}s before restart attempt $count..."
        _notify "⚠️ TASO crashed ($crash_type, attempt $count/$MAX_SAME_CRASH). Restarting in ${wait_time}s..."
        sleep "$wait_time"
    done

    _log "=== Watchdog exiting ==="
}

main "$@"
