---
name: check
description: Run the AutoM2M admission checker (W1-W6) on a typed-team JSON file or on the current AutoM2M team, and explain each violation and how to fix it. Use when the user asks whether a team design is well-formed, or after editing a typed team.
argument-hint: "[typed-team JSON file]"
---

# Check a typed team (W1-W6)

1. If `$ARGUMENTS` names a JSON file, read it and call `auto_check` with its content as `team_json`.
   Otherwise call `auto_check` without arguments (the current `.agentm2m/auto/` team).
2. Report `ADMITTED` or `REJECTED`, then group the diagnostics by condition:
   - **W1 well typed**: paths, bound features, validators and their argument types.
   - **W2 one writer**: each view has one owning agent; each class is created by one rule.
   - **W3 complete**: every mandatory feature bound exactly once.
   - **W4 anchored coverage**: every goal must flow into a value checked by a *behaviour* validator, and
     every view must be on such a flow (no idle or unverified agents).
   - **W5 engine decides done**: `done` is exactly `cover(G), valid, fresh, noObl`; no hand-off cycles.
   - **W6 right tools**: the owner of an LLM value has the tools its validators need (`exec`).
3. For each violation propose the smallest concrete JSON change. Warnings are informational.
4. If the user agrees and the team is the workspace's, apply the change with `auto_submit_team`.
