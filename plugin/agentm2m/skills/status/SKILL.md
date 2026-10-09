---
name: status
description: Show the state of the agentm2m team - agents and views they own, hand-offs, trace links, binding states, open obligations and escalations, and whether phi holds; for an AutoM2M team also the task, admission history and failing phi clauses. Use when the user asks where the team stands or what depends on what.
argument-hint: "[element key to trace, e.g. Criterion#S2.1]"
---

# Team status

A project can hold a hand-written AgentM2M team (`.agentm2m/team.yaml`), an AutoM2M team
(`.agentm2m/auto/`), or both. Report whichever exists.

1. Call `team_status` and `auto_status`.
   - Neither has anything: offer `/agentm2m:init` (hand-written team from a template) or
     `/agentm2m:auto-build` (Claude builds a checked team for a Python task).
2. **AgentM2M team** (`team_status` has a workspace): team name and backend; a table of hand-offs
   (sources -> target, trace links, binding states); open items grouped by owning agent; phi.
3. **AutoM2M team** (`auto_status` has a task or team):
   - the task (class or function, methods to implement);
   - admission: rounds so far, and if the last proposal was rejected, its W1-W6 diagnostics;
   - the admitted team: agents, views, hand-offs;
   - the run: phi, `open_clauses`, pending and blocked values per agent, and each failure with its
     rule, binding, agent and reason;
   - next step: pending values -> `/agentm2m:auto-build` (continue filling); phi false with nothing pending
     -> `/agentm2m:diagnose`; phi true -> `auto_deliverable`.
4. If `$ARGUMENTS` is an element key, call `trace_query` for it and show upstream sources and the
   downstream closure (everything derived from it, per hand-off).
5. If the user asks what a planned change would affect, use `impact` (it never calls an LLM).
