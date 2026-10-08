"""MCP server exposing an agentm2m workspace to Claude Code (stdio).

    agentm2m-mcp                    # serves <cwd>/.agentm2m
    AGENTM2M_PROJECT_DIR=/repo agentm2m-mcp
    AGENTM2M_LLM=host|mock|ollama|anthropic|openai   (default: host)

In the default *host* mode the engine never calls an LLM itself: stochastic
bindings come back from `next_bindings` as footprint-bounded prompts and
Claude Code answers them through `submit_binding`, where the same @check
validators decide acceptance. Every tool is a thin wrapper around
`agentm2m.workspace.Workspace`.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml

from . import __version__
from .workspace import Workspace, WorkspaceError, list_templates

try:  # MCP Python SDK 2.x
    from mcp.server.mcpserver import MCPServer as _Server
    from mcp.server.mcpserver.exceptions import ToolError
except ImportError:  # pragma: no cover - SDK 1.x
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
"""


def _project_dir() -> Path:
    return Path(os.getenv("AGENTM2M_PROJECT_DIR") or os.getenv("CLAUDE_PROJECT_DIR") or os.getcwd())


_workspaces: dict[str, Workspace] = {}


def _ws() -> Workspace:
    key = str(_project_dir().resolve())
    if key not in _workspaces:
        _workspaces[key] = Workspace(key)
    return _workspaces[key]


def _call(fn, *args, **kwargs) -> dict:
    try:
        return fn(*args, **kwargs)
    except WorkspaceError as exc:
        # A deliberate, actionable tool error (shown to the model verbatim),
        # not a crash with a traceback.
        raise ToolError(str(exc)) from None
    except Exception as exc:  # e.g. an OCL error in a user's rule module
        raise ToolError(f"{type(exc).__name__}: {exc}") from exc


mcp = _Server("agentm2m", instructions=INSTRUCTIONS)


@mcp.tool()
def team_init(template: str = "devteam", force: bool = False) -> dict:
    """Create the agentm2m workspace (.agentm2m/) in the project from a template
    (devteam, research, incident). force=true replaces an existing workspace
    and discards its state."""
    return _call(_ws().init, template, force=force)


@mcp.tool()
def team_status() -> dict:
    """Team overview: agents and the views they own (write rights), hand-offs,
    trace links, binding states (fresh/missing/stale/escalated), open items,
    and whether the acceptance predicate phi holds."""
    ws = _ws()
    if not ws.exists():
        return {"workspace": None, "project_dir": str(ws.project_dir), "templates": list_templates(),
                "hint": "no .agentm2m/ workspace yet; call team_init"}
    return _call(ws.status)


@mcp.tool()
def team_validate() -> dict:
    """Validate team.yaml and every hand-off rule module: parsing, declared
    views and classes, root slots, helper paths, and that rules can match."""
    return _call(_ws().validate)


@mcp.tool()
def model_show(view: str, key: str | None = None, depth: int = 3) -> dict:
    """Show a view model (or one element by key, e.g. 'UserStory#S2').
    Elements created by hand-offs are marked engine_owned."""
    return _call(_ws().show, view, key, depth)


@mcp.tool()
def model_edit(view: str, ops: list[dict[str, Any]], as_agent: str) -> dict:
    """Edit a view as the agent that owns it (write rights are enforced; elements
    created by hand-offs are read-only). ops is a list of:
    {"op":"create","feature":"stories","parent":"<key, optional>","value":{attr: value, ref: "Type#id", containment: [{...}]}}
    {"op":"set","key":"Criterion#S2.1","values":{"text":"..."}}
    {"op":"delete","key":"UserStory#S3"}
    Atomic: if any op fails nothing changes."""
    return _call(_ws().edit, view, ops, as_agent)


@mcp.tool()
def impact(view: str | None = None, ops: list[dict[str, Any]] | None = None, as_agent: str | None = None) -> dict:
    """Preview Obl(Delta) without calling any LLM or changing anything: which
    stochastic bindings a change obliges to be re-sampled (and which elements
    would be created/deleted). Pass view+ops+as_agent to preview an edit
    before applying it; without ops it previews the current state."""
    return _call(_ws().impact, view, ops, as_agent)


@mcp.tool()
def run(max_passes: int | None = None) -> dict:
    """Run every hand-off to a fixpoint: match, create/delete targets, resolve
    references, update trace links. In host mode stale @llm bindings are
    returned as pending (fill them with next_bindings/submit_binding); with
    an engine backend they are sampled and validated directly."""
    return _call(_ws().run, max_passes)


@mcp.tool()
def next_bindings(agent: str | None = None, limit: int = 5) -> dict:
    """Host mode: the next @llm bindings to fill. Each carries its complete,
    footprint-bounded prompt; answer from that prompt only and submit the bare
    value with submit_binding. Optionally filter by owning agent."""
    return _call(_ws().next_bindings, agent, limit)


@mcp.tool()
def submit_binding(target_key: str, binding: str, value: str, footprint_version: str) -> dict:
    """Host mode: submit a value for one pending binding, passing the
    footprint_version that came with its prompt. Status is accepted, rejected
    (with reason and a retry_prompt; resubmit), escalated (validator kept
    rejecting: needs a source change or a human), already_accepted, or stale
    (the footprint changed since the prompt was issued: fetch it again)."""
    return _call(_ws().submit_binding, target_key, binding, value, footprint_version)


@mcp.tool()
def trace_query(key: str, transitive: bool = True) -> dict:
    """Trace links for an element key (e.g. 'Criterion#S2.1'): what it was
    generated from (upstream) and everything generated from it (downstream,
    transitively by default)."""
    return _call(_ws().trace_query, key, transitive)


@mcp.tool()
def team_evolve(
    agent: str,
    view: str,
    handoff: str,
    rule: str,
    view_spec: dict[str, Any] | None = None,
    view_spec_file: str | None = None,
    rule_text: str | None = None,
) -> dict:
    """Higher-order transformation: add a new agent owning a new view, connected
    by a new hand-off rule module, while the team is running. Give the view as
    view_spec (same shape as a view in team.yaml, without owner) or as a YAML
    file path relative to .agentm2m/. rule is a path relative to .agentm2m/;
    pass rule_text to create that file. Existing matches become obligations
    for the new agent on the next run."""
    ws = _ws()
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
def acceptance() -> dict:
    """The acceptance predicate phi, decided by the engine: every match covered
    by a trace link and every @llm value accepted for its current footprint
    (nothing pending, stale, or escalated). Lists what is still open."""
    return _call(_ws().acceptance)


def main() -> None:
    import sys

    if "--version" in sys.argv:
        print(__version__)
        return
    mcp.run()


if __name__ == "__main__":
    main()
