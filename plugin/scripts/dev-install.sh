#!/usr/bin/env bash
# Install the plugin into Claude Code from this checkout, with the MCP server
# and hooks running the LOCAL engine (no PyPI release needed).
#
#   plugin/scripts/dev-install.sh            # install / reinstall
#   plugin/scripts/dev-install.sh --remove   # uninstall and drop the dev marketplace
source "$(dirname "$0")/_common.sh"
command -v claude >/dev/null || die "claude CLI not found"
if [ "${1:-}" = "--remove" ]; then
  claude plugin uninstall agentm2m@agentm2m-dev || true
  claude plugin marketplace remove agentm2m-dev || true
  exit 0
fi
command -v uvx >/dev/null || die "uv is required (https://docs.astral.sh/uv/): the MCP server starts with uvx"
log "validating plugin"
claude plugin validate --strict "$PLUGIN_DIR"
log "checking the local engine starts through uvx"
uvx --from "$REPO_ROOT" --with "mcp>=1.2" agentm2m-mcp --version
log "registering dev marketplace + installing"
claude plugin marketplace remove agentm2m-dev >/dev/null 2>&1 || true
claude plugin marketplace add "$PLUGIN_ROOT"
claude plugin install agentm2m@agentm2m-dev
cat <<MSG

Installed agentm2m@agentm2m-dev. Until the engine is on PyPI, start Claude Code with the local engine:

  export AGENTM2M_ENGINE="$REPO_ROOT"
  claude

Then in any scratch repo: /agentm2m:init devteam, /agentm2m:run
Uninstall with: $0 --remove
MSG
