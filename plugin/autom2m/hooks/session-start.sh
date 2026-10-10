#!/usr/bin/env bash
# Announce an agenthot workspace in the project, without starting the engine
# (keeps session start instant and offline).
set -u
dir="${CLAUDE_PROJECT_DIR:-$PWD}/.agenthot"
if [ -f "$dir/team.yaml" ]; then
  name=$(sed -n 's/^name:[[:space:]]*//p' "$dir/team.yaml" | head -n1)
  echo "agenthot workspace detected at .agenthot/ (team: ${name:-unnamed}). Hand-offs between its agents are M2M transformations run by the AgentHOT MCP server: use /autom2m:status to inspect, /autom2m:run to execute, /autom2m:change for requirement changes. Never edit .agenthot/state/ by hand."
fi
if [ -f "$dir/auto/team.json" ]; then
  echo "AutoM2M team detected at .autom2m/ (checked by W1-W6). Use auto_status for phi and pending values, /autom2m:auto-build to continue the run, /autom2m:diagnose if phi fails, /autom2m:check after design edits. Change the team only through auto_submit_team; never edit .autom2m/state.json by hand."
elif [ -f "$dir/auto/task.json" ]; then
  echo "AutoM2M task set at .autom2m/ but no admitted team yet: continue with /autom2m:auto-build."
fi
exit 0
