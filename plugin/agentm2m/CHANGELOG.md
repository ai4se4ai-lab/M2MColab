# Changelog

## 0.3.0

AutoM2M in the plugin, and a hosted service.

- 12 new MCP tools for AutoM2M (typed teams proposed by a builder, admitted by the deterministic W1-W6
  checker): `auto_task_set`, `auto_check`, `auto_propose`, `auto_submit_team`, `auto_run`,
  `auto_next_bindings`, `auto_submit_binding`, `auto_status`, `auto_attribute`, `auto_deliverable`,
  `auto_solve`, `auto_reset`. State persists in `.agentm2m/auto/` (task, team, run state, history).
- Host mode for AutoM2M: Claude is the builder (propose / revise / delta prompts) and fills the values;
  the library validators (which execute the code) and phi decide.
- Skills `/agentm2m:auto-build`, `/agentm2m:check`, `/agentm2m:diagnose`; agents `team-builder` and
  `auto-binding-worker`; `handoff-architect` checks typed teams with `auto_check`.
- Hooks: session start announces an AutoM2M team; edits to `.agentm2m/auto/team.json` or
  `*.typed-team.json` are re-checked with W1-W6.
- Remote use: the same tools are served over streamable HTTP at `/mcp` by `agentm2m serve`, with
  self-service API keys. Setting `AGENTM2M_URL` and `AGENTM2M_API_KEY` (optionally `AGENTM2M_PROJECT`)
  makes the plugin's MCP server forward every call there, so skills and agents work unchanged.
- `/agentm2m:status` and the background concepts cover AutoM2M teams.
- Security: code that validators execute goes through one sandbox (`AGENTM2M_SANDBOX`); `team_evolve`'s
  `rule_text` can only create `.agentm2m` files; rule expressions cannot reach private/dunder attributes.

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
