# Shared by the plugin scripts: repo paths and the python/uv to use.
set -euo pipefail
SCRIPTS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PLUGIN_ROOT="$(cd "$SCRIPTS_DIR/.." && pwd)"          # plugin/
PLUGIN_DIR="$PLUGIN_ROOT/agentm2m"                     # plugin/agentm2m (the plugin)
REPO_ROOT="$(cd "$PLUGIN_ROOT/.." && pwd)"
if [ -x "$REPO_ROOT/.venv/bin/python" ]; then PY="$REPO_ROOT/.venv/bin/python"; else PY="$(command -v python3)"; fi
export PATH="$REPO_ROOT/.venv/bin:$PATH"
VERSION="$("$PY" -c 'import re,sys; print(re.search(r"^version = \"([^\"]+)\"", open(sys.argv[1]).read(), re.M).group(1))' "$REPO_ROOT/pyproject.toml")"
log() { printf '\033[1m==> %s\033[0m\n' "$*"; }
die() { printf 'error: %s\n' "$*" >&2; exit 1; }
