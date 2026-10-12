# Changelog

## 0.4.0

The plugin is now **autom2m** (it was agentm2m); the runtime is **AgentHOT**, aligned with the paper.

- Renames: plugin and MCP server `autom2m` (tools `mcp__plugin_autom2m_autom2m__*`), engine package
  `autom2m` with the Python packages `agenthot` (compiler, runtime) and `autom2m` (team level); CLI
  `agenthot` and `autom2m`; rule files `*.agenthot`, workspaces `.agenthot/`, AutoM2M state `.autom2m/`;
  environment variables `AGENTHOT_*`.
- Goal view with `Example` objects (doctests leave the docstrings); obligations `(class, scope, mode,
  anchors)`; acceptance clause `noEsc` (was `noObl`) and library clause `examples_pass`.
- Checker: W1 stratification and multiplicity, two-sided feature-level anchoring in W4 (with one-sided
  and path-only variants for comparison), totality, diagnostics with hints, Listing 6 output verbatim.
- Runtime: stamps over footprint and validator reads, cover(G) through recorded reads, typed LLM values.
- Attribution per Algorithm 4 (stability, adaptive replays, widening by up to two references with
  minimisation, upstream recursion); repair applied in place, by extension or by rebuild.

## 0.3.0

AutoM2M in the plugin, and a hosted service.

- 12 new MCP tools for AutoM2M (typed teams proposed by a builder, admitted by the deterministic W1-W6
  checker): `auto_task_set`, `auto_check`, `auto_propose`, `auto_submit_team`, `auto_run`,
  `auto_next_bindings`, `auto_submit_binding`, `auto_status`, `auto_attribute`, `auto_deliverable`,
  `auto_solve`, `auto_reset`. State persists in `.autom2m/` (task, team, run state, history).
- Host mode for AutoM2M: Claude is the builder (propose / revise / delta prompts) and fills the values;
  the library validators (which execute the code) and phi decide.
- Skills `/autom2m:auto-build`, `/autom2m:check`, `/autom2m:diagnose`; agents `team-builder` and
  `auto-binding-worker`; `handoff-architect` checks typed teams with `auto_check`.
- Hooks: session start announces an AutoM2M team; edits to `.autom2m/team.json` or
  `*.typed-team.json` are re-checked with W1-W6.
- Remote use: the same tools are served over streamable HTTP at `/mcp` by `autom2m serve`, with
  self-service API keys. Setting `AGENTHOT_URL` and `AGENTHOT_API_KEY` (optionally `AGENTHOT_PROJECT`)
  makes the plugin's MCP server forward every call there, so skills and agents work unchanged.
- `/autom2m:status` and the background concepts cover AutoM2M teams.
- Security: code that validators execute goes through one sandbox (`AGENTHOT_SANDBOX`); `team_evolve`'s
  `rule_text` can only create `.agenthot` files; rule expressions cannot reach private/dunder attributes.

## 0.2.0

First plugin release.

- MCP server `autom2m` (engine `autom2m==0.2.0` via `uvx`): 12 tools covering the team lifecycle
  (`team_init`, `team_status`, `team_validate`, `model_show`, `model_edit`, `impact`, `run`,
  `next_bindings`, `submit_binding`, `trace_query`, `team_evolve`, `acceptance`).
- Host mode (default): Claude Code fills `@llm` bindings from their footprint-bounded prompts; values
  pass the same `@check` validators and escalation budget as engine-sampled ones.
- Skills: `/autom2m:init`, `run`, `change`, `evolve`, `status`, `author-handoff`, plus background
  concepts. Agents: `binding-worker`, `handoff-architect`.
- Hooks: workspace announcement at session start; re-validation after edits to the team spec, rules, or helpers.
- Templates: `devteam` (the paper's running example, with a Security Reviewer HOT extension), `research`
  (n:m hand-off), `incident` (Lift binding, executable-oracle validator).
