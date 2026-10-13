#!/bin/bash
# One-shot audit of a run (no polling loop — rerun it by hand, or use --watch N with N >= 60):
#   bash hpc/status.sh [RUN_ID]              colour audit: jobs > steps > substeps, then data progress
#   bash hpc/status.sh [RUN_ID] --watch      live table, redrawn every 60 s (--watch 120 = 2 min); ends when all jobs ended
#   bash hpc/status.sh [RUN_ID] --only failed
# RUN_ID defaults to the most recent run in hpc/logs/. Snapshots are saved as logs/<RUN_ID>/audit.txt and audit.json.
HPC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RUN_ID=""; if [[ "${1:-}" == am2m-* ]]; then RUN_ID="$1"; shift; fi
RUN_ID="${RUN_ID:-$(ls -1t "$HPC_DIR/logs" 2>/dev/null | head -1)}"
[ -n "$RUN_ID" ] || { echo "no runs in $HPC_DIR/logs"; exit 1; }
python3 "$HPC_DIR/scripts/audit.py" "$RUN_ID" "$@" || exit $?
case " $* " in *" --watch "*) exit 0 ;; esac
echo; python3 "$HPC_DIR/scripts/progress.py" "$RUN_ID"
