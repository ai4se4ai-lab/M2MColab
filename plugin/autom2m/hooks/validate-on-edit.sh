#!/usr/bin/env bash
# PostToolUse: after Claude edits the team spec, a rule module, helpers or a
# typed team (AutoM2M), re-validate and feed errors back (exit 2 -> shown to Claude).
set -u
input=$(cat)
file=$(printf '%s' "$input" | python3 -c 'import json,sys
try:
    d = json.load(sys.stdin); print((d.get("tool_input") or {}).get("file_path") or "")
except Exception:
    print("")' 2>/dev/null)
mode=workspace
case "$file" in
  */.agenthot/state/*) exit 0 ;;
  */.autom2m/team.json|*.typed-team.json) mode=auto ;;
  */.autom2m/*) exit 0 ;;
  */.agenthot/*.agenthot|*/.agenthot/*team.yaml|*/.agenthot/*helpers.py|*/.agenthot/*.view.yaml) ;;
  *) exit 0 ;;
esac
project="${CLAUDE_PROJECT_DIR:-${file%%/.agenthot/*}}"
command -v uvx >/dev/null 2>&1 || exit 0   # engine not installable here: stay silent
engine="${AUTOM2M_ENGINE:-autom2m==0.4.0}"
if [ "$mode" = auto ]; then
  if out=$(uvx --from "$engine" autom2m auto check "$file" 2>&1); then
    exit 0
  fi
  printf 'AutoM2M checker rejected %s:\n%s\nFix the diagnostics (or submit through auto_submit_team).\n' "$file" "$out" >&2
  exit 2
fi
if out=$(uvx --from "$engine" agenthot workspace --dir "$project" validate --brief 2>&1); then
  exit 0
fi
printf '%s\n' "$out" >&2
exit 2
