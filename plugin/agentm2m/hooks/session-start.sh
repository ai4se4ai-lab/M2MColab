#!/usr/bin/env bash
# Announce an agentm2m workspace in the project, without starting the engine
# (keeps session start instant and offline).
set -u
dir="${CLAUDE_PROJECT_DIR:-$PWD}/.agentm2m"
[ -f "$dir/team.yaml" ] || exit 0
name=$(sed -n 's/^name:[[:space:]]*//p' "$dir/team.yaml" | head -n1)
echo "agentm2m workspace detected at .agentm2m/ (team: ${name:-unnamed}). Hand-offs between its agents are M2M transformations run by the agentm2m MCP server: use /agentm2m:status to inspect, /agentm2m:run to execute, /agentm2m:change for requirement changes. Never edit .agentm2m/state/ by hand."
exit 0
