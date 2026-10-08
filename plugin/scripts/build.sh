#!/usr/bin/env bash
# Build the engine distribution (wheel + sdist) and a zip of the plugin.
# Verifies the wheel really contains what the plugin needs at run time.
source "$(dirname "$0")/_common.sh"
"$PY" "$SCRIPTS_DIR/bump_version.py" --check
cd "$REPO_ROOT"
rm -rf dist build
log "python -m build"
"$PY" -m build --sdist --wheel --outdir dist . >/dev/null
"$PY" -m twine check dist/*
wheel="$(ls dist/agentm2m-"$VERSION"-*.whl)"
log "wheel contents check"
"$PY" - "$wheel" <<'PYEOF'
import sys, zipfile
names = set(zipfile.ZipFile(sys.argv[1]).namelist())
need = ["agentm2m/mcp_server.py", "agentm2m/rules/grammar.lark", "agentm2m/templates/devteam/team.yaml",
        "agentm2m/templates/devteam/rules/helpers.py", "agentm2m/templates/devteam/rules/extra/Arch2Sec.agentm2m",
        "agentm2m/templates/research/team.yaml", "agentm2m/templates/incident/team.yaml"]
missing = [n for n in need if n not in names]
eps = [n for n in names if n.endswith("entry_points.txt")]
ep = zipfile.ZipFile(sys.argv[1]).read(eps[0]).decode() if eps else ""
if missing or "agentm2m-mcp" not in ep:
    sys.exit(f"wheel is incomplete: missing={missing} entry_points_ok={'agentm2m-mcp' in ep}")
print(f"ok: {len(names)} files")
PYEOF
log "smoke test: fresh install of the wheel via uvx"
uvx --isolated --from "$wheel" --with "mcp>=1.2" agentm2m-mcp --version
log "plugin zip"
(cd "$PLUGIN_ROOT" && zip -qr "$REPO_ROOT/dist/agentm2m-plugin-$VERSION.zip" agentm2m -x '*/__pycache__/*')
ls -1 dist
