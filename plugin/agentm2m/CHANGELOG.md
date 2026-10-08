# Changelog

## 0.2.0

First plugin release.

- MCP server `agentm2m` (engine `agentm2m==0.2.0` via `uvx`): 12 tools covering the team lifecycle
  (`team_init`, `team_status`, `team_validate`, `model_show`, `model_edit`, `impact`, `run`,
  `next_bindings`, `submit_binding`, `trace_query`, `team_evolve`, `acceptance`).
- Host mode (default): Claude Code fills `@llm` bindings from their footprint-bounded prompts; values
  pass the same `@check` validators and escalation budget as engine-sampled ones.
- Skills: `/agentm2m:init`, `run`, `change`, `evolve`, `status`, `author-handoff`, plus background
  concepts. Agents: `binding-worker`, `handoff-architect`.
- Hooks: workspace announcement at session start; re-validation after edits to the team spec, rules, or helpers.
- Templates: `devteam` (the paper's running example, with a Security Reviewer HOT extension), `research`
  (n:m hand-off), `incident` (Lift binding, executable-oracle validator).
