#!/usr/bin/env bash
# Everything CI checks: plugin + marketplace manifests (strict), template
# rules parse and validate, and both test suites.
source "$(dirname "$0")/_common.sh"
if command -v claude >/dev/null; then
  log "claude plugin validate --strict"
  claude plugin validate --strict "$PLUGIN_DIR"
  claude plugin validate --strict "$PLUGIN_ROOT"
else
  log "claude CLI not found: skipping manifest validation (JSON syntax still checked)"
fi
log "JSON syntax"
for f in "$PLUGIN_DIR/.claude-plugin/plugin.json" "$PLUGIN_DIR/.mcp.json" "$PLUGIN_DIR/hooks/hooks.json" "$PLUGIN_ROOT/.claude-plugin/marketplace.json"; do
  "$PY" -m json.tool "$f" >/dev/null || die "invalid JSON: $f"
done
log "version consistency ($VERSION)"
"$PY" "$SCRIPTS_DIR/bump_version.py" --check
log "templates validate"
tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT
for t in $("$PY" -m agenthot.cli workspace templates); do
  "$PY" -m agenthot.cli workspace --dir "$tmp/$t" --llm mock init "$t" >/dev/null
  "$PY" -m agenthot.cli workspace --dir "$tmp/$t" validate --brief
done
log "tests"
"$PY" -m pytest -q "$REPO_ROOT/tests" "$PLUGIN_ROOT/tests"
