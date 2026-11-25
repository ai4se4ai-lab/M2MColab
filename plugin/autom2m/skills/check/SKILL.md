---
name: check
description: Run the AutoM2M admission checker (W1-W6) on a typed-team JSON file or on the current AutoM2M team, and explain each violation and how to fix it. Use when the user asks whether a team design is well-formed, or after editing a typed team.
argument-hint: "[typed-team JSON file]"
---

# Check a typed team (W1-W6)

1. If `$ARGUMENTS` names a JSON file, read it and call `auto_check` with its content as `team_json`.
   Otherwise call `auto_check` without arguments (the current `.autom2m/` team).
2. Report `ADMITTED` or `REJECTED`, then group the diagnostics by condition:
   - **W1 well typed and stratified**: paths, bound features, validators and their argument types; no
     guard or structural binding reads a value an LLM writes.
   - **W2 one writer**: each view has one owning agent; each class is created by one rule.
   - **W3 complete**: every mandatory feature bound exactly once; references resolve.
   - **W4 every goal anchored**: the anchor features of each `checked` goal reach one behaviour-checked
     value both through its footprint (the producer sees them) and through its validator's arguments (the
     check tests against them); each `delivered` goal reaches the deliverable; every view lies on such a
     flow (no idle or unverified agents).
   - **W5 engine decides done**: `done` has the four engine clauses `cover(G), valid, fresh, noEsc` (plus
     optional library clauses such as `examples_pass`) and no agent claim; no hand-off cycles.
   - **W6 right tools**: the owner of an LLM value has the tools its validators need (`exec`).
3. For each violation propose the smallest concrete JSON change. Warnings are informational.
4. If the user agrees and the team is the workspace's, apply the change with `auto_submit_team`.
