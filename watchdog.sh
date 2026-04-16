#!/usr/bin/env bash
# Watchdog: restart Phase 2 supervisor if it's not running and compile isn't done.
PDF_DIR="/home/user/Test-Game"
COMPILED="$PDF_DIR/tsr02141 Encyclopedia Magica Volume One_compiled.md"
SUPERVISOR="$PDF_DIR/run_phase2.sh"
LOG="$PDF_DIR/run_phase2_supervisor.log"
API_KEY="${ANTHROPIC_API_KEY}"

# Count compiled pages
DONE=$(grep -c "<!-- page " "$COMPILED" 2>/dev/null || echo 0)

# Already finished?
if [ "$DONE" -ge 416 ]; then
    exit 0
fi

# Already running?
if pgrep -f "run_phase2.sh" > /dev/null 2>&1; then
    exit 0
fi

# Not running — restart
echo "[$(date '+%Y-%m-%d %H:%M:%S')] Watchdog restarting supervisor (pages: $DONE/416)" >> "$LOG"
ANTHROPIC_API_KEY="$API_KEY" nohup bash "$SUPERVISOR" >> "$LOG" 2>&1 &
