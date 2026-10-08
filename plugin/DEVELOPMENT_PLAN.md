# agentm2m as a Claude Code plugin: development plan

## Status (2026-09-30): Phases 1-4 implemented; Phase 5 partly

**Done and tested:** engine additions, MCP server, plugin, scripts, CI.
- 109 tests pass: 39 original + 70 new. The new ones are 21 in `tests/` and 49 in `plugin/tests/`.
- `claude plugin validate --strict` passes for the plugin and the marketplace.
- `release.sh` dry run passes: build, `twine check`, a fresh `uvx` install of the wheel, the plugin zip, and the directory entry.
- Live end-to-end runs in Claude Code (`--plugin-dir`, engine via `uvx`) all worked:
  - `/agentm2m:init` created the workspace.
  - `/agentm2m:run` reached φ = true, with `binding-worker` subagents filling the values.
  - `/agentm2m:change`: tightening S2.1 re-derived exactly `{op_S2.signature, tc_S2.1.oracle, ed_S2.body}`. Nothing of S1 changed.
  - `/agentm2m:evolve` added a SecurityReviewer, which received 4 retroactive obligations. 11/11 bindings ended fresh.
- Marketplace `add` and `install` were checked in an isolated `CLAUDE_CONFIG_DIR`.

**Not done yet (on purpose):** publishing to PyPI, making the repo public, and submitting to the directory. All wait until after the double-anonymous review. `release.sh --publish` refuses to run unless `AGENTM2M_ALLOW_PUBLIC=1` is set, and the CI publish job needs the repo variable `ALLOW_PUBLIC=true`. Until the engine is on PyPI, set `AGENTM2M_ENGINE=/path/to/checkout` when using the plugin.

**Deviations from the plan below, and why:**
- **Templates ship inside the engine package** (`src/agentm2m/templates/`), not in `plugin/agentm2m/templates/`. This way the MCP server finds them wherever uvx installs it.
- **Workspace state is a JSON store** (`agentm2m/store.py`), not XMI plus a sidecar. Cross-view references across several XMI resources are fragile in pyecore, and the store also keeps engine target keys.
- **The service layer is `agentm2m/workspace.py`.** `mcp_server.py` is a thin wrapper over it and works with MCP SDK 1.x (`FastMCP`) and 2.x (`MCPServer`). The CLI also gained `agentm2m workspace …`.
- **Additions found during live testing:**
  1. `submit_binding` requires the prompt's `footprint_version`. A value written for an outdated footprint is refused as stale. Without this, a code body written against the old signature was stamped against the new one.
  2. `model_edit` re-runs `R^str` immediately, with no sampling. Without this, status and φ lagged behind structural copies.
  3. Bindings whose footprint reads an unfilled upstream value are reported as *blocked*, not offered with empty context.
- **Template T2 reads the criteria as well as the signature,** which T1 copies structurally onto the operation. This matches the paper (Sec. III Step 6 / Sec. V), so a tightened criterion reaches the code.
- **Other fixes made along the way:**
  - `grammar.lark` was missing from package data, so any non-editable install would have been broken.
  - `extends: X` given as a single string was mis-parsed.
  - `[tool.uv]` gained `cache-keys`, so uvx rebuilds a local checkout when `src/` changes, and a 7-day `exclude-newer` cooldown.

**Developer quickstart:** `pip install -e ".[dev]"`, then `plugin/scripts/validate.sh`, then `plugin/scripts/dev-install.sh`, then start `claude` with `AGENTM2M_ENGINE` set.

---

## Context

`agentm2m` (under `src/agentm2m/`) implements the approach in `docs/DS-A2A.tex`:
- Every hand-off between agents is an ATL-style M2M transformation.
- A deterministic engine handles matching, element creation, reference resolution, and trace links.
- The LLM only fills `@llm` stochastic bindings. It sees only the binding's footprint, and its answer is kept only if it passes `@check`.
- Traces are persisted, so a change creates exact obligations (RQ2: precision and recall of 1.00).
- A HOT adds agents at runtime.

Today the engine is usable only as a library. Teams are wired in Python (`examples/*/run.py`), state is in-process only (see the `_amt_target_key` limitation in `docs/ARCHITECTURE.md`), and the LLM is an external backend.

**Goal:** a Claude Code plugin in a new `plugin/` directory that anyone can install with `/plugin install`. With it, Claude Code:
- sets up an agentm2m team in any repo,
- runs hand-offs through the engine,
- fills stochastic bindings itself (inside the footprint limit),
- propagates changes through trace obligations,
- evolves the team with a HOT.

Build and release scripts register the plugin in a self-hosted marketplace and prepare the submission to Anthropic's official directory.

**Decisions made with the user:**
1. **LLM:** Claude Code fills the bindings by default ("host mode"). The existing ollama, anthropic, openai, and mock backends remain as a fallback for unattended runs.
2. **Engine distribution:** publish to PyPI and run it with `uvx`, pinned per plugin version.
3. **Release timing:** build and test privately now. Public GitHub, PyPI, and directory submission happen **after the double-anonymous review**. Release scripts default to dry-run and refuse to publish unless `AGENTM2M_ALLOW_PUBLIC=1` is set.

**Step 0 of implementation:** copy this plan into `plugin/DEVELOPMENT_PLAN.md`.

---

## Target layout

```
plugin/
├── DEVELOPMENT_PLAN.md
├── .claude-plugin/marketplace.json      # dev/self-hosted marketplace "agentm2m" -> ./agentm2m
├── agentm2m/                            # THE plugin (immutable slug: "agentm2m")
│   ├── .claude-plugin/plugin.json       # name, version, description, author, homepage, license, keywords
│   ├── .mcp.json                        # agentm2m MCP server via uvx (pinned)
│   ├── skills/                          # user-invocable → /agentm2m:<name>
│   │   ├── init/SKILL.md                # scaffold .agentm2m/ from a template or from the repo
│   │   ├── run/SKILL.md                 # run to fixpoint, fill pending bindings, report phi
│   │   ├── change/SKILL.md              # edit a view, show impact (Obl), propagate
│   │   ├── evolve/SKILL.md              # HOT: add an agent + hand-off at runtime
│   │   ├── status/SKILL.md              # team, traces, obligations, escalations
│   │   ├── author-handoff/SKILL.md      # help write metamodel YAML + .agentm2m rules + helpers
│   │   └── agentm2m-concepts/SKILL.md   # model-invoked background: views, footprints, phi, rules DSL
│   ├── agents/
│   │   ├── binding-worker.md            # fills ONE pending binding; tools = only mcp__agentm2m__binding_* (no Read/Grep → footprint discipline)
│   │   └── handoff-architect.md         # designs view metamodels/rules from a repo's docs
│   ├── hooks/hooks.json                 # SessionStart status line; PostToolUse validate on *.agentm2m / team.yaml edits
│   ├── hooks/*.sh
│   ├── templates/{devteam,research,incident}/  # team.yaml, rules/*.agentm2m, helpers.py, seeds/*.json
│   ├── README.md, LICENSE (MIT), CHANGELOG.md
├── scripts/
│   ├── dev-install.sh        # add local marketplace + install plugin, engine from local checkout
│   ├── validate.sh           # `claude plugin validate`, JSON-schema checks, rule parse-check, pytest
│   ├── bump_version.py       # one version → pyproject, plugin.json, marketplace.json, .mcp.json pin
│   ├── build.sh              # python -m build (wheel+sdist), twine check, zip plugin dir
│   ├── release.sh            # guarded: TestPyPI → PyPI, git tag, root marketplace.json sha, entry JSON
│   └── submission_entry.py   # emits the official-directory marketplace entry (git-subdir, ref, sha)
└── tests/                    # plugin-level tests (MCP tools, host mode, persistence, structure)
```

Also at the repo root, created by `release.sh` and not needed for development: `.claude-plugin/marketplace.json`. It points to `./plugin/agentm2m`, so `/plugin marketplace add <owner>/<repo>` works once the repo is public. `.github/workflows/plugin.yml` runs CI.

---

## Phase 1: Engine additions (`src/agentm2m/`, small and backward-compatible)

All current tests and examples must pass unchanged.

1. **Declarative team spec:** `team/spec.py`, plus `pyyaml` added as a dependency.
   - `load_team(dir) -> Team` reads `.agentm2m/team.yaml`.
   - The YAML declares views (classes, attributes, references including cross-view `Arch.Operation`, root slots, owner agent), hand-offs (rule path, target view), and optional seed JSON.
   - It builds everything through the existing `MetamodelBuilder` (`metamodel/builder.py`) and `Team.add_agent/add_view/add_handoff` (`team/model.py`).
   - This removes the need to write Python the way `examples/01_devteam/metamodels.py` does.
2. **Cross-process persistence:** `workspace.py`.
   - `save_workspace` / `load_workspace` store models as XMI (reusing `metamodel/io.save_model/load_model`) and traces as JSON (reusing `TraceModel.save/load`).
   - A sidecar `target_keys.json` maps each engine-created element's `eURIFragment()` to its `_amt_target_key` and restores it on load. This fixes the "incremental re-execution is in-process only" limitation in `docs/ARCHITECTURE.md`.
   - A file lock covers `.agentm2m/state/`.
3. **Host-mode backend:** Claude Code fills the bindings.
   - Add `llm/host_backend.py`. Its `HostBackend.generate` raises `PendingSample`.
   - Split `engine/binding.apply_stochastic_binding` so the accept path is a shared `accept_sample(binding, target_var, obj, match, link, raw, helpers) -> (ok, reason)`. It covers `@check`, Lift, stamps, and `failed_stamps`.
   - When the engine catches `PendingSample`, it records a `PendingBinding(target_key, binding, rule, prompt, fp_digest, attempt)` in a new `HandoffReport.pending` and in `TeamRunReport`.
   - New `TeamRuntime.submit_binding(target_key, binding, value)` does the following:
     1. Re-evaluates the footprint and rejects the submission as stale if the digest has changed.
     2. Calls `accept_sample`.
     3. On rejection, returns the reason and the `_retry_prompt` text, and counts the attempt in `TraceLink` (new persisted `attempts` field).
     4. After `max_resamples` rejections, sets `failed_stamps` and escalates. This is the same semantics as Algorithm 1.
   - Acceptance (`acceptance_holds`) is false while any binding is pending.
4. **Impact preview:** `TeamRuntime.impact() -> list[Obligation]`. It runs the structural phase with `HostBackend` against a deep copy, and every pending binding it produces is exactly `Obl(Δ)`. There are no LLM calls.
5. **HOT from spec:** `team/hot.py` gets `TeamChange.from_spec(yaml_fragment, team)`, which reuses spec parsing for the new view.
6. Add the optional dependency extra `mcp = ["mcp>=1.2"]` and the entry point `agentm2m-mcp = "agentm2m.mcp_server:main"`.

## Phase 2: MCP server (`src/agentm2m/mcp_server.py`, FastMCP over stdio)

The server holds one `TeamRuntime` per workspace, loaded from `.agentm2m/` in the Claude Code project dir, and saves after every mutating tool. Tools:

| Tool | Wraps |
|---|---|
| `team_init(template, dir)` | copy `templates/<t>` → `.agentm2m/`, `load_team` |
| `team_status()` | agents, views, ω, hand-offs, counts, phi, pending/escalations |
| `team_validate()` | spec load + `parse_module_file` per rule (reuse `cli.cmd_validate` logic) |
| `model_show(view, key?)` | view model as compact JSON |
| `model_edit(view, ops, as_agent)` | create/set/delete; enforces ω and "hand-off targets are engine-owned" (Step 5 of paper) |
| `impact()` | `TeamRuntime.impact()`: preview Obl(Δ) |
| `run(max_passes)` | `run_to_fixpoint()` → summary + pending list |
| `binding_next(agent?)` / `binding_submit(target_key, binding, value)` | host mode loop |
| `trace_query(key)` | upstream/downstream links via `TraceModel.find_by_source_key` |
| `team_evolve(spec_fragment, rule_path)` | `apply_hot` |
| `acceptance()` | `TeamRuntime.acceptance_holds()` |

Backend selection uses `AGENTM2M_LLM=host|ollama|anthropic|openai|mock` (default `host`) and reuses `llm/factory.make_backend` and `config.LLMConfig`.

## Phase 3: Plugin contents (`plugin/agentm2m/`)

- **`.mcp.json`:** `{"agentm2m": {"command": "uvx", "args": ["--from", "${AGENTM2M_ENGINE:-agentm2m[mcp]==X.Y.Z}", "agentm2m-mcp"], "env": {"AGENTM2M_LLM": "${AGENTM2M_LLM:-host}"}}}`. Developers point `AGENTM2M_ENGINE` at the local checkout.
- **Skills:**
  - `run` tells Claude to loop `run` → for each pending binding → dispatch the `binding-worker` subagent, which sees only prompt + footprint → `binding_submit` → repeat until nothing is pending → report phi and escalations.
  - `change` does `model_edit` → `impact` (shown to the user before any spend) → `run`.
- **`binding-worker` agent:** its tool allowlist is only `mcp__agentm2m__binding_next` and `mcp__agentm2m__binding_submit`. This carries the paper's footprint-bounded prompting into Claude Code, since the worker cannot read the repo. Model: `inherit`.
- **Hooks:**
  - SessionStart prints a one-line team status if `.agentm2m/` exists.
  - PostToolUse (`Edit|Write`) runs `agentm2m validate` when the edited file is `*.agentm2m` or `team.yaml`.
  - Both check that `uv` is available and fail soft.
- **Templates:** convert `examples/01_devteam` (and 04 and 05) to `team.yaml` + rules + helpers, copying the existing `.agentm2m` rule files and `helpers.py`.
- **README:** install, the host vs. backend modes, a warning that `uses 'helpers.py'` executes project Python, the privacy note (footprints only), and a worked DevTeam walkthrough.

## Phase 4: Build, register, and publish scripts (`plugin/scripts/`)

- **`dev-install.sh`:** `claude plugin marketplace add ./plugin` → `claude plugin install agentm2m@agentm2m-dev`. It exports `AGENTM2M_ENGINE=$(repo)` so uvx uses the local engine.
- **`validate.sh`:** `claude plugin validate plugin/agentm2m` and the marketplace file; `python -m json.tool` on all JSON files; `agentm2m validate` on every template rule; `pytest tests plugin/tests`.
- **`bump_version.py X.Y.Z`:** keeps a single version in sync across `pyproject.toml`, `plugin.json`, both `marketplace.json` files, and the `.mcp.json` pin.
- **`build.sh`:** `python -m build`, `twine check dist/*`, and a zip of `plugin/agentm2m` for manual sharing.
- **`release.sh [--dry-run default] [--test-pypi|--pypi]`:**
  - Refuses to run unless `AGENTM2M_ALLOW_PUBLIC=1`, the git tree is clean, and the version matches everywhere.
  - Uploads with twine (TestPyPI first), tags `vX.Y.Z`, and writes the root `.claude-plugin/marketplace.json`.
  - Runs `submission_entry.py` to print the official-directory entry: `git-subdir`, `path: plugin/agentm2m`, the `ref` tag, the `sha`, category `development`, and homepage.
  - Prints the link to the submission form (`https://clau.de/plugin-directory-submission`). The form itself is manual and cannot be scripted, and Anthropic reviews every submission.
- **CI (`.github/workflows/plugin.yml`):** tests + validate + build on PR; on a tag, publish to PyPI with trusted publishing (OIDC, no stored token).

## Phase 5: Hardening before directory submission

- Pin the `mcp` version and test on Python 3.11 and 3.12 on Linux and macOS.
- Load `helpers.py` only from inside the workspace, and document that loading it runs code.
- Refuse path traversal in `team_init` and `rule_path`.
- Set a timeout on validator exec.
- Make MCP tool outputs concise, with long footprints truncated with a marker.
- Optionally add a `claude plugin eval` suite that exercises init → run → change.

---

## Verification

1. Run `pytest tests/ -q`. All existing engine tests must still pass (backward compatibility).
2. Run the new `plugin/tests/`:
   - **Host-mode round trip on the DevTeam template:** the first `run` returns pending bindings. Submitting mock-valid values reaches phi = True. A bad value returns a reason, and after k bad values the binding escalates without being resampled on an unchanged footprint.
   - **Persistence:** run in process A and save. Load in process B, tighten `S2.1`, and check that `impact()` equals exactly `{op_S2.signature, tc_S2.1.oracle, ed_S2.body}`, the paper's Step 6 example.
   - **HOT via `team_evolve`:** the reviewer gets obligations for every existing Operation.
   - **MCP:** an in-process FastMCP client calls every tool.
3. Run `plugin/scripts/validate.sh` and `claude plugin validate`, and confirm both are clean.
4. Run `plugin/scripts/dev-install.sh`. Then, in a fresh scratch repo in Claude Code:
   1. `/agentm2m:init devteam`
   2. `/agentm2m:run` should end with phi true, with bindings filled by `binding-worker`.
   3. `/agentm2m:change` "S2.1 must return HTTP 409" should show the impact preview and re-sample only those bindings.
   4. `/agentm2m:evolve` should add a security reviewer.
5. Run `release.sh --dry-run --test-pypi`. It should build and print the entry JSON without uploading. A real TestPyPI upload + `uvx --index-url test.pypi…` smoke test happens only after the review.
