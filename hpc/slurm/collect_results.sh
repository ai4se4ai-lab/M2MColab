#!/bin/bash
# Copy a run's results from scratch to project: collect_results.sh <results dir> <destination>
# Everything except the per-run JSON files is copied as is; results/runs/ (tens of
# thousands of small files) becomes one archive, runs.tar.gz (Alliance guidance on many
# small files). Called by the analysis stage; safe to rerun.
set -euo pipefail
SRC="$1"; DEST="$2"
mkdir -p "$DEST"
rsync -a --delete --exclude 'runs/' --exclude 'runs.tar.gz' "$SRC"/ "$DEST"/
if [ -d "$SRC/runs" ]; then
    tar -C "$SRC" -czf "$DEST/runs.tar.gz.tmp" runs && mv "$DEST/runs.tar.gz.tmp" "$DEST/runs.tar.gz"
fi
if [ -d "$SRC/docs_out" ]; then   # the paper's file names, at the top level for convenience
    rsync -a "$SRC/docs_out/tables" "$SRC/docs_out/figures" "$DEST"/ 2>/dev/null || true
fi
[ -f "$(dirname "$SRC")/run_manifest.json" ] && cp "$(dirname "$SRC")/run_manifest.json" "$DEST"/
du -sh "$DEST" | sed 's/^/collected: /'
