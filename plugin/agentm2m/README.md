# agentm2m for Claude Code

Run a team of agents (Analyst, Architect, Developer, Tester, or your own roles) whose hand-offs are
**model-to-model transformations** instead of free text:

- **Nothing is silently dropped.** Each agent owns a typed view model. A deterministic engine creates
  every downstream element, reference, and trace link, so every accepted user story gets its
  operation and every acceptance criterion gets its test case.
- **Claude fills only values, and only from their footprint.** An `@llm` binding's prompt carries
  exactly the source data it may use. The value is kept only if its `@check` validator accepts it.
  After k rejections the binding escalates to you instead of looping.
- **Change impact is exact.** Every accepted value is stamped with its footprint. Edit a requirement
  and only the bindings that read it are redone. `impact` previews them before any tokens are spent.
- **The team can grow while it runs.** Adding a reviewer is a higher-order transformation. The new
  agent immediately receives work for every existing element, with no glue code.

## Install

Requires [uv](https://docs.astral.sh/uv/) (the engine is a Python package started with `uvx`) and Python 3.11+.

```
/plugin marketplace add <owner>/<repo>
/plugin install agentm2m@agentm2m
```

## Use

```
/agentm2m:init devteam            # .agentm2m/ with the 4-agent DevTeam
/agentm2m:run                     # engine builds structure; binding-worker agents fill values
/agentm2m:change S2.1 must return HTTP 409 when the task is already done
/agentm2m:evolve add a security reviewer for every API operation
/agentm2m:status Criterion#S2.1   # what was derived from this criterion
```

Templates: `devteam` (the running example of the agentm2m paper), `research` (multi-source hand-off),
`incident` (Lift binding and executable validator). Custom teams: `/agentm2m:author-handoff`.

### AutoM2M: let Claude build the team, let the checker admit it

```
/agentm2m:auto-build calculator.py   # task -> typed team -> W1-W6 check -> run -> phi -> code
/agentm2m:check team.typed-team.json # W1-W6 on a typed team, with fixes for each violation
/agentm2m:diagnose                   # phi failed: locate the fault, propose a checked repair
```

The task is a Python class whose methods to implement are stubs (docstring with `>>>` examples, body
`pass` / `...` / `raise NotImplementedError`), or one stub function. Claude acts as the **builder** and
writes a typed team (agents, forms, hand-off rules, footprints, library validators); the deterministic
checker admits it only if W1-W6 hold (well typed, one writer, complete, anchored coverage, the engine
decides done, right tools). The engine then runs it; Claude fills each value from its footprint, and the
library validators (which execute the code against the documented examples and the team's tests)
decide. The run is done only when the engine's phi holds. State lives in `.agentm2m/auto/`.

## Use the hosted service instead of a local engine

The same tools are served over HTTP by `agentm2m serve` (web app, REST API and `/mcp` in one process).
Create an API key on its web page (Services -> API keys). Then either:

**Keep the plugin, run on the service.** Start Claude Code with

```
AGENTM2M_URL=https://<host> AGENTM2M_API_KEY=<api key> claude
```

(optionally `AGENTM2M_PROJECT=<name>`; every key has its own projects). The plugin's MCP server then
forwards every tool call to the service under the same tool names, so all skills, subagents and hooks
work unchanged; nothing is stored in `.agentm2m/` locally.

**Or connect without the plugin:**

```
claude mcp add --transport http agentm2m https://<host>/mcp --header "Authorization: Bearer <api key>"
```

(tools are then named `mcp__agentm2m__*`; add `--header "X-AgentM2M-Project: <name>"` to pick a project).

A hosted server executes submitted code only in an isolating sandbox (see the main README); its Status
tab shows whether code execution is enabled.

## How values get produced

| `AGENTM2M_LLM` | Who fills `@llm` bindings |
|---|---|
| `host` (default) | Claude Code, through `next_bindings` / `submit_binding`. No extra API key. |
| `anthropic`, `openai`, `ollama` | The engine calls that API itself (set `ANTHROPIC_API_KEY` / `OPENAI_API_KEY`, or run Ollama). Good for unattended runs. |
| `mock` | Deterministic canned values, for demos and tests. |

Set it in the environment Claude Code starts in, e.g. `AGENTM2M_LLM=anthropic LLM_MODEL=claude-haiku-4-5-20251001 claude`.

## Files

- `.agentm2m/team.yaml`: views (classes, owner agent, seed model) and hand-offs.
- `.agentm2m/rules/*.agentm2m`: hand-off rules (ATL-style, with `@llm` and `@check`).
- `.agentm2m/rules/helpers.py`: validators and helper functions.
- `.agentm2m/state/state.json`: view models, trace links, stamps, and runtime team changes. Managed by the engine.
- `.agentm2m/auto/`: AutoM2M `task.json`, the admitted `team.json`, `state.json` (run state) and
  `history.json` (admission rounds, runs, faults). Managed by the engine; change the team with `auto_submit_team`.

## Security and privacy

- `helpers.py` in your workspace is **executed** by the engine. Treat it like any other code in the
  repo, and only open workspaces you trust.
- Template validators that check generated code (test oracles, dry-run scripts) run it in a separate
  Python process with a timeout, not in a sandbox.
- In host mode no data leaves Claude Code beyond your normal session. With an API backend, only binding
  prompts (instruction plus footprint) are sent to that provider.

## Troubleshooting

- *MCP server does not start*: check that `uvx --version` works. Pin a different engine build with
  `AGENTM2M_ENGINE` (e.g. a local checkout path).
- *Edit rejected: engine-owned*: that element is generated by a hand-off. Change its source view instead.
- *Escalations*: the validator kept rejecting values for that footprint. Change the source (e.g. make
  the criterion clearer) or relax the validator in `helpers.py`.
