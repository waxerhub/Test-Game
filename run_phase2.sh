#!/usr/bin/env bash
# Keeps restarting Phase 2 with --resume until all 416 pages are compiled.
set -euo pipefail

PDF="tsr02141 Encyclopedia Magica Volume One.pdf"
TOTAL=416
WORKERS=3
LOG="phase2_log.txt"

while true; do
    # Count already-compiled pages
    DONE=0
    if [ -f "${PDF%.pdf}_compiled.md" ]; then
        DONE=$(grep -c "<!-- page " "${PDF%.pdf}_compiled.md" 2>/dev/null || true)
    fi

    echo "[$(date '+%H:%M:%S')] Pages compiled: $DONE / $TOTAL"

    if [ "$DONE" -ge "$TOTAL" ]; then
        echo "[$(date '+%H:%M:%S')] All $TOTAL pages done!"
        break
    fi

    echo "[$(date '+%H:%M:%S')] Starting compiler (--resume, $WORKERS workers)…"
    python3 compiler.py "$PDF" --workers "$WORKERS" --resume >> "$LOG" 2>&1 || true

    echo "[$(date '+%H:%M:%S')] Process exited — waiting 5s before restart…"
    sleep 5
done
