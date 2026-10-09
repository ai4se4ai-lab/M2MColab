#!/usr/bin/env bash
# Announce an agentm2m workspace in the project, without starting the engine
# (keeps session start instant and offline).
set -u
dir="${CLAUDE_PROJECT_DIR:-$PWD}/.agentm2m"
if [ -f "$dir/team.yaml" ]; then
  name=$(sed -n 's/^name:[[:space:]]*//p' "$dir/team.yaml" | head -n1)
  echo "agentm2m workspace detected at .agentm2m/ (team: ${name:-unnamed}). Hand-offs between its agents are M2M transformations run by the agentm2m MCP server: use /agentm2m:status to inspect, /agentm2m:run to execute, /agentm2m:change for requirement changes. Never edit .agentm2m/state/ by hand."
fi
if [ -f "$dir/auto/team.json" ]; then
  echo "AutoM2M team detected at .agentm2m/auto/ (checked by W1-W6). Use auto_status for phi and pending values, /agentm2m:auto-build to continue the run, /agentm2m:diagnose if phi fails, /agentm2m:check after design edits. Change the team only through auto_submit_team; never edit .agentm2m/auto/state.json by hand."
elif [ -f "$dir/auto/task.json" ]; then
  echo "AutoM2M task set at .agentm2m/auto/ but no admitted team yet: continue with /agentm2m:auto-build."
fi
exit 0
