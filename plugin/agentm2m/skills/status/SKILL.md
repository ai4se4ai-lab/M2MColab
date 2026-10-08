---
name: status
description: Show the state of the agentm2m team - agents and views they own, hand-offs, trace links, binding states, open obligations and escalations, and whether phi holds. Use when the user asks where the team stands or what depends on what.
argument-hint: "[element key to trace, e.g. Criterion#S2.1]"
---

# Team status

1. Call `team_status`. If there is no workspace, offer `/agentm2m:init`.
2. Present compactly: team name and backend; a table of hand-offs (sources -> target, trace links,
   binding states); open items grouped by owning agent; phi.
3. If `$ARGUMENTS` is an element key, call `trace_query` for it and show upstream sources and the
   downstream closure (everything derived from it, per hand-off).
4. If the user asks what a planned change would affect, use `impact` (it never calls an LLM).
