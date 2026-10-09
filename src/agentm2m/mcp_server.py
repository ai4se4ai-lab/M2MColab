"""MCP server exposing an agentm2m workspace to Claude Code.

    agentm2m-mcp                    # stdio; serves <cwd>/.agentm2m
    AGENTM2M_PROJECT_DIR=/repo agentm2m-mcp
    AGENTM2M_LLM=host|mock|ollama|anthropic|openai   (default: host)
    AGENTM2M_SOLVE_LLM=ollama|anthropic|...          (engine LLM for auto_solve)

The same server is mounted at /mcp by the hosted service (`agentm2m serve`,
streamable HTTP). There the auth middleware resolves the API key and passes
the tenant id in the `x-agentm2m-tenant` header; each tenant gets its own
project directories (`X-AgentM2M-Project` picks one, default "default").

In the default *host* mode the engine never calls an LLM itself: stochastic
bindings come back from `next_bindings` as footprint-bounded prompts and
Claude Code answers them through `submit_binding`, where the same @check
validators decide acceptance. Every tool is a thin wrapper around
`agentm2m.workspace.Workspace`.
"""
from __future__ import annotations

import functools
import inspect
import os
import re
from pathlib import Path
from typing import Any

import yaml

from . import __version__
from . import remote as _remote
from .auto.sandbox import SandboxRefused
from .auto.workspace import AutoWorkspace, check_team_json
from .workspace import Workspace, WorkspaceError, list_templates

try:  # MCP Python SDK 2.x
    from mcp.server.mcpserver import Context
    from mcp.server.mcpserver import MCPServer as _Server
    from mcp.server.mcpserver.exceptions import ToolError
except ImportError:  # pragma: no cover - SDK 1.x
    from mcp.server.fastmcp import Context  # type: ignore[no-redef]
    from mcp.server.fastmcp import FastMCP as _Server  # type: ignore[no-redef]
    from mcp.server.fastmcp.exceptions import ToolError  # type: ignore[no-redef]

INSTRUCTIONS = """\
agentm2m runs a team of agents whose hand-offs are model-to-model transformations.
Each agent owns one view model; a deterministic engine creates target elements,
references and trace links; only @llm attribute values need an LLM, and each is
accepted only if its @check validator passes. Typical loop: team_status ->
run -> next_bindings -> submit_binding (repeat) -> acceptance. For a change:
model_edit (as the owning agent) -> impact -> run. Answer a binding ONLY from the
prompt next_bindings returns: it already contains the whole allowed footprint.

AutoM2M (auto_* tools) builds the team itself for a Python task: auto_task_set
(the skeleton) -> auto_propose (you are the builder: answer with a typed-team
JSON) -> auto_submit_team (the W1-W6 checker admits or returns diagnostics;
revise until admitted) -> auto_run -> auto_next_bindings / auto_submit_binding
(repeat) -> auto_status (phi) -> auto_deliverable. If phi fails: auto_attribute,
then auto_propose(mode="delta") and auto_submit_team with the revised team.
auto_check runs W1-W6 on any typed team without changing anything.
"""

TENANT_HEADER = "x-agentm2m-tenant"
PROJECT_HEADER = "x-agentm2m-project"
_PROJECT_RE = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")


def data_dir() -> Path:
    return Path(os.getenv("AGENTM2M_DATA_DIR") or Path.home() / ".agentm2m-service")


def tenant_project_dir(tenant: str, project: str | None) -> Path:
    project = (project or "default").strip()
    if not _PROJECT_RE.match(project) or project in (".", ".."):
        raise ToolError("invalid project name (use letters, digits, '.', '_' or '-', at most 64)")
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", tenant):
        raise ToolError("invalid tenant")
    d = data_dir() / "tenants" / tenant / project
    d.mkdir(parents=True, exist_ok=True)
    return d


def _project_dir(ctx: Context | None = None) -> Path:
    headers = None
    if ctx is not None:
        try:
            headers = ctx.headers
        except (ValueError, AttributeError):
            headers = None
    if headers:
        tenant = headers.get(TENANT_HEADER)
        if tenant:  # set by the hosted service's auth middleware only
            return tenant_project_dir(tenant, headers.get(PROJECT_HEADER))
        raise ToolError("unauthenticated request: send an API key (Authorization: Bearer <key>)")
    return Path(os.getenv("AGENTM2M_PROJECT_DIR") or os.getenv("CLAUDE_PROJECT_DIR") or os.getcwd())


_workspaces: dict[str, Workspace] = {}
_auto: dict[str, AutoWorkspace] = {}


def _ws(ctx: Context | None = None) -> Workspace:
    key = str(_project_dir(ctx).resolve())
    if key not in _workspaces:
        _workspaces[key] = Workspace(key)
    return _workspaces[key]


def _aws(ctx: Context | None = None) -> AutoWorkspace:
    return auto_workspace(_project_dir(ctx))


def auto_workspace(project_dir: Path) -> AutoWorkspace:
    """The shared AutoWorkspace for a directory (MCP and REST use the same one)."""
    key = str(Path(project_dir).resolve())
    if key not in _auto:
        _auto[key] = AutoWorkspace(key)
    return _auto[key]


def solve_backend() -> str | None:
    """The engine LLM used by auto_solve, if one is configured."""
    b = (os.getenv("AGENTM2M_SOLVE_LLM") or "").strip().lower()
    if b and b != "host":
        return b
    b = (os.getenv("AGENTM2M_LLM") or "host").strip().lower()
    return None if b == "host" else b


def _call(fn, *args, **kwargs) -> dict:
    try:
        return fn(*args, **kwargs)
    except (WorkspaceError, SandboxRefused) as exc:
        # A deliberate, actionable tool error (shown to the model verbatim),
        # not a crash with a traceback.
        raise ToolError(str(exc)) from None
    except Exception as exc:  # e.g. an OCL error in a user's rule module
        raise ToolError(f"{type(exc).__name__}: {exc}") from exc


mcp = _Server("agentm2m", instructions=INSTRUCTIONS)


def _forwardable(fn):
    """Run the tool here, or, when the plugin is pointed at a hosted service
    (AGENTM2M_URL + AGENTM2M_API_KEY), forward the same call there. Calls
    arriving over HTTP (i.e. on the hosted service itself) always run here."""
    sig = inspect.signature(fn)

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        ctx = kwargs.get("ctx")
        over_http = False
        if ctx is not None:
            try:
                over_http = bool(ctx.headers)
            except (ValueError, AttributeError):
                over_http = False
        rc = None if over_http else _remote_client()
        if rc is None:
            return fn(*args, **kwargs)
        bound = sig.bind(*args, **kwargs)
        arguments = {k: v for k, v in bound.arguments.items() if k != "ctx"}
        try:
            return rc.call_tool(fn.__name__, arguments)
        except (_remote.RemoteError, _remote.RemoteToolError) as exc:
            raise ToolError(str(exc)) from None

    return wrapper


def _remote_client():
    try:
        return _remote.client()
    except _remote.RemoteError as exc:
        raise ToolError(str(exc)) from None


@mcp.tool()
@_forwardable
def team_init(template: str = "devteam", force: bool = False, ctx: Context | None = None) -> dict:
    """Create the agentm2m workspace (.agentm2m/) in the project from a template
    (devteam, research, incident). force=true replaces an existing workspace
    and discards its state."""
    return _call(_ws(ctx).init, template, force=force)


@mcp.tool()
@_forwardable
def team_status(ctx: Context | None = None) -> dict:
    """Team overview: agents and the views they own (write rights), hand-offs,
    trace links, binding states (fresh/missing/stale/escalated), open items,
    and whether the acceptance predicate phi holds."""
    ws = _ws(ctx)
    if not ws.exists():
        return {"workspace": None, "project_dir": str(ws.project_dir), "templates": list_templates(),
                "hint": "no .agentm2m/ workspace yet; call team_init"}
    return _call(ws.status)


@mcp.tool()
@_forwardable
def team_validate(ctx: Context | None = None) -> dict:
    """Validate team.yaml and every hand-off rule module: parsing, declared
    views and classes, root slots, helper paths, and that rules can match."""
    return _call(_ws(ctx).validate)


@mcp.tool()
@_forwardable
def model_show(view: str, key: str | None = None, depth: int = 3, ctx: Context | None = None) -> dict:
    """Show a view model (or one element by key, e.g. 'UserStory#S2').
    Elements created by hand-offs are marked engine_owned."""
    return _call(_ws(ctx).show, view, key, depth)


@mcp.tool()
@_forwardable
def model_edit(view: str, ops: list[dict[str, Any]], as_agent: str, ctx: Context | None = None) -> dict:
    """Edit a view as the agent that owns it (write rights are enforced; elements
    created by hand-offs are read-only). ops is a list of:
    {"op":"create","feature":"stories","parent":"<key, optional>","value":{attr: value, ref: "Type#id", containment: [{...}]}}
    {"op":"set","key":"Criterion#S2.1","values":{"text":"..."}}
    {"op":"delete","key":"UserStory#S3"}
    Atomic: if any op fails nothing changes."""
    return _call(_ws(ctx).edit, view, ops, as_agent)


@mcp.tool()
@_forwardable
def impact(view: str | None = None, ops: list[dict[str, Any]] | None = None, as_agent: str | None = None, ctx: Context | None = None) -> dict:
    """Preview Obl(Delta) without calling any LLM or changing anything: which
    stochastic bindings a change obliges to be re-sampled (and which elements
    would be created/deleted). Pass view+ops+as_agent to preview an edit
    before applying it; without ops it previews the current state."""
    return _call(_ws(ctx).impact, view, ops, as_agent)


@mcp.tool()
@_forwardable
def run(max_passes: int | None = None, ctx: Context | None = None) -> dict:
    """Run every hand-off to a fixpoint: match, create/delete targets, resolve
    references, update trace links. In host mode stale @llm bindings are
    returned as pending (fill them with next_bindings/submit_binding); with
    an engine backend they are sampled and validated directly."""
    return _call(_ws(ctx).run, max_passes)


@mcp.tool()
@_forwardable
def next_bindings(agent: str | None = None, limit: int = 5, ctx: Context | None = None) -> dict:
    """Host mode: the next @llm bindings to fill. Each carries its complete,
    footprint-bounded prompt; answer from that prompt only and submit the bare
    value with submit_binding. Optionally filter by owning agent."""
    return _call(_ws(ctx).next_bindings, agent, limit)


@mcp.tool()
@_forwardable
def submit_binding(target_key: str, binding: str, value: str, footprint_version: str, ctx: Context | None = None) -> dict:
    """Host mode: submit a value for one pending binding, passing the
    footprint_version that came with its prompt. Status is accepted, rejected
    (with reason and a retry_prompt; resubmit), escalated (validator kept
    rejecting: needs a source change or a human), already_accepted, or stale
    (the footprint changed since the prompt was issued: fetch it again)."""
    return _call(_ws(ctx).submit_binding, target_key, binding, value, footprint_version)


@mcp.tool()
@_forwardable
def trace_query(key: str, transitive: bool = True, ctx: Context | None = None) -> dict:
    """Trace links for an element key (e.g. 'Criterion#S2.1'): what it was
    generated from (upstream) and everything generated from it (downstream,
    transitively by default)."""
    return _call(_ws(ctx).trace_query, key, transitive)


@mcp.tool()
@_forwardable
def team_evolve(
    agent: str,
    view: str,
    handoff: str,
    rule: str,
    view_spec: dict[str, Any] | None = None,
    view_spec_file: str | None = None,
    rule_text: str | None = None,
    ctx: Context | None = None,
) -> dict:
    """Higher-order transformation: add a new agent owning a new view, connected
    by a new hand-off rule module, while the team is running. Give the view as
    view_spec (same shape as a view in team.yaml, without owner) or as a YAML
    file path relative to .agentm2m/. rule is a path relative to .agentm2m/;
    pass rule_text to create that file. Existing matches become obligations
    for the new agent on the next run."""
    ws = _ws(ctx)
    if view_spec is None:
        if not view_spec_file:
            raise ToolError("give view_spec or view_spec_file")
        p = (ws.dir / view_spec_file).resolve()
        try:
            p.relative_to(ws.dir.resolve())
        except ValueError:
            raise ToolError("view_spec_file must be inside .agentm2m/") from None
        if not p.is_file():
            raise ToolError(f"{view_spec_file} not found under {ws.dir}")
        view_spec = yaml.safe_load(p.read_text()) or {}
    return _call(ws.evolve, agent, view, view_spec, handoff, rule, rule_text)


@mcp.tool()
@_forwardable
def acceptance(ctx: Context | None = None) -> dict:
    """The acceptance predicate phi, decided by the engine: every match covered
    by a trace link and every @llm value accepted for its current footprint
    (nothing pending, stale, or escalated). Lists what is still open."""
    return _call(_ws(ctx).acceptance)


# ---------------------------------------------------------------------------
# AutoM2M: the team is proposed by a builder (you, in host mode) and admitted
# by the deterministic W1-W6 checker; state lives in .agentm2m/auto/
# ---------------------------------------------------------------------------


@mcp.tool()
@_forwardable
def auto_task_set(source: str, description: str = "", ctx: Context | None = None) -> dict:
    """AutoM2M step 0: set the task as Python source: a class whose methods to
    implement are stubs (docstring + pass / ... / raise NotImplementedError,
    ideally with >>> examples), or one stub function. It is lifted into the
    fixed Goal view (Task, Method). Replacing the task resets the run state."""
    return _call(_aws(ctx).set_task, source, description)


@mcp.tool()
@_forwardable
def auto_check(team_json: dict[str, Any] | str | None = None, naive: bool = False, ctx: Context | None = None) -> dict:
    """Run the AutoM2M admission checker (W1 well typed, W2 one writer, W3
    complete, W4 anchored coverage, W5 engine decides done / acyclic, W6
    right tools) on a typed team, without changing anything. Without
    team_json it checks the workspace's current team. naive=true uses
    class-level W4 (for comparison only)."""
    if team_json is not None:
        return _call(check_team_json, team_json, naive=naive)
    return _call(_aws(ctx).check, None, naive=naive)


@mcp.tool()
@_forwardable
def auto_propose(mode: str = "auto", ctx: Context | None = None) -> dict:
    """AutoM2M steps A/C/F: the builder prompt. mode=propose (first team),
    revise (fix the diagnostics of the last rejected proposal), delta (repair
    after phi failed, from fault reports), or auto (picks one). Answer the
    prompt yourself with ONE typed-team JSON object and pass it to
    auto_submit_team."""
    return _call(_aws(ctx).builder_prompt, mode)


@mcp.tool()
@_forwardable
def auto_submit_team(team_json: dict[str, Any] | str, ctx: Context | None = None) -> dict:
    """Submit a typed team. The checker admits it (it is compiled onto the
    AgentM2M runtime) or rejects it with exact W1-W6 diagnostics to fix. If a
    team is already running, an admitted team is applied as a checked delta:
    in-place / hot (accepted values kept) or rebuild."""
    return _call(_aws(ctx).submit_team, team_json)


@mcp.tool()
@_forwardable
def auto_run(ctx: Context | None = None) -> dict:
    """AutoM2M step D: run every hand-off to a fixpoint and evaluate
    phi = cover(G) and valid and fresh and noObl. In host mode open LLM values
    come back as pending (fill them with auto_next_bindings /
    auto_submit_binding); with an engine backend they are sampled directly."""
    return _call(_aws(ctx).run)


@mcp.tool()
@_forwardable
def auto_next_bindings(agent: str | None = None, limit: int = 5, ctx: Context | None = None) -> dict:
    """Host mode: the next AutoM2M values to fill, each with its complete,
    footprint-bounded prompt (role, task, output format, context). Answer
    ONLY from that prompt, then call auto_submit_binding. Bindings that wait
    on an upstream value are held back until it is accepted."""
    return _call(_aws(ctx).next_bindings, agent, limit)


@mcp.tool()
@_forwardable
def auto_submit_binding(target_key: str, binding: str, value: str, footprint_version: str,
                        ctx: Context | None = None) -> dict:
    """Host mode: submit one value with the footprint_version its prompt came
    with. The library validators (they execute the code against the
    documented examples / tests) decide: accepted, rejected (reason +
    retry_prompt), escalated, already_accepted or stale."""
    return _call(_aws(ctx).submit_binding, target_key, binding, value, footprint_version)


@mcp.tool()
@_forwardable
def auto_status(ctx: Context | None = None) -> dict:
    """AutoM2M overview: task, admitted team (agents, views, hand-offs),
    the last rejected proposal's diagnostics, phi and its failing clauses,
    pending bindings and recent history."""
    return _call(_aws(ctx).status)


@mcp.tool()
@_forwardable
def auto_attribute(limit: int = 2, ctx: Context | None = None) -> dict:
    """AutoM2M step E: locate every failed phi clause by trace lookup (hand-off,
    rule, binding, owning agent). With an engine LLM each fault is also
    classified by bounded replay (validator / sampling / footprint / upstream
    / specification); in host mode faults are located only."""
    return _call(_aws(ctx).attribute, limit)


@mcp.tool()
@_forwardable
def auto_deliverable(ctx: Context | None = None) -> dict:
    """The assembled Python code (accepted values, best effort for the rest)
    and which methods are accepted."""
    return _call(_aws(ctx).deliverable)


@mcp.tool()
@_forwardable
def auto_solve(source: str | None = None, ctx: Context | None = None) -> dict:
    """Run the whole AutoM2M loop unattended with the server's engine LLM
    (builder, checker, run, attribution, repair). Only available when the
    server has an engine LLM configured (AGENTM2M_SOLVE_LLM); in host mode use
    the step-by-step tools instead."""
    backend = solve_backend()
    if backend is None:
        raise ToolError("auto_solve is not available: no engine LLM is configured on this server "
                        "(AGENTM2M_SOLVE_LLM); drive the loop step by step with auto_propose / auto_submit_team / "
                        "auto_run / auto_next_bindings")
    aw = _aws(ctx)
    return _call(AutoWorkspace(aw.project_dir, backend=backend).solve, source)


@mcp.tool()
@_forwardable
def auto_reset(ctx: Context | None = None) -> dict:
    """Delete the AutoM2M state (.agentm2m/auto/): task, team and run."""
    aw = _aws(ctx)
    _auto.pop(str(aw.project_dir), None)
    return _call(aw.reset)


def main() -> None:
    import sys

    if "--version" in sys.argv:
        print(__version__)
        return
    settings = _remote.remote_settings()
    if settings:
        print(f"agentm2m-mcp: forwarding every tool call to {settings[0]}/mcp"
              + (f" (project {settings[2]})" if settings[2] else ""), file=sys.stderr)
    mcp.run()


if __name__ == "__main__":
    main()
