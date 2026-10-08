---
name: init
description: Set up an agentm2m team workspace (.agentm2m/) in this project from a built-in template (devteam, research, incident) or tailored to the project. Use when the user wants to start a structured multi-agent team, or asks for agentm2m in a repo that has none.
argument-hint: "[devteam|research|incident|custom]"
---

# Set up an agentm2m team

1. Call the agentm2m `team_status` tool. If a workspace already exists, show its summary and ask
   before replacing it (replacing uses `team_init` with `force: true` and discards all state).
2. Pick the template:
   - `$ARGUMENTS` if it names one of `devteam`, `research`, `incident`.
   - `custom` or a project-specific request: start from the closest template, then hand the design
     of views and rules to the `handoff-architect` agent (see `/agentm2m:author-handoff`).
   - Otherwise `devteam` (Analyst -> Architect -> Developer, plus Analyst -> Tester).
3. Call `team_init` with that template, then `team_validate`.
4. Report in a few lines: agents and the view each owns, the hand-offs (source -> target), how many
   stochastic bindings wait to be filled, and the next step (`/agentm2m:run`).

Files created: `.agentm2m/team.yaml` (views, owners, seed models, hand-offs), `.agentm2m/rules/`
(hand-off modules and `helpers.py` validators), `.agentm2m/state/` (engine state; never edit by hand).
Suggest committing `team.yaml` and `rules/`; `state/` can be committed too if the team wants a shared
trace history.
