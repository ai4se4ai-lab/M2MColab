"""agentm2m CLI: small utilities around the engine.

Scenario orchestration itself (building metamodels, seeding models, wiring
a Team, running to a fixpoint) is Python code -- see any `examples/*/run.py`
-- because that's what building view metamodels and seed data actually
requires. The CLI covers the cross-cutting bits: checking a rule file
parses, inspecting a saved trace, and checking the configured LLM backend
is reachable before you burn a run on it.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .config import LLMConfig
from .engine.trace import TraceModel
from .llm.factory import make_backend
from .rules.parser import parse_module_file


def cmd_validate(args: argparse.Namespace) -> int:
    path = Path(args.rule_file)
    try:
        module = parse_module_file(path)
    except Exception as exc:  # noqa: BLE001
        print(f"INVALID: {path}: {exc}", file=sys.stderr)
        return 1
    print(f"OK: {path} -- module {module.name}, {len(module.rules)} rule(s):")
    for rule in module.rules:
        n_struct = sum(1 for tp in rule.to_clause.patterns for b in tp.bindings if hasattr(b, "expr"))
        n_stoch = sum(1 for tp in rule.to_clause.patterns for b in tp.bindings if hasattr(b, "prompt_expr"))
        print(f"  - {rule.name}: {n_struct} structural binding(s), {n_stoch} stochastic binding(s)")
    return 0


def cmd_trace_show(args: argparse.Namespace) -> int:
    path = Path(args.trace_file)
    trace = TraceModel.load(path)
    payload = {"handoff": trace.handoff, "links": [l.to_dict() for l in trace.links()]}
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


def cmd_llm_check(args: argparse.Namespace) -> int:
    cfg = LLMConfig.from_env()
    provider = args.provider or cfg.provider
    model = args.model or cfg.model
    print(f"provider={provider} model={model}")
    try:
        backend = make_backend(cfg, override_provider=provider, override_model=model)
        reply = backend.generate("Reply with the single word: ok", temperature=0.0)
    except Exception as exc:  # noqa: BLE001
        print(f"FAILED: {exc}", file=sys.stderr)
        return 1
    print(f"OK: backend responded ({len(reply)} chars): {reply[:80]!r}")
    return 0


def cmd_workspace(args: argparse.Namespace) -> int:
    from .workspace import Workspace, WorkspaceError, list_templates

    if args.ws_command == "templates":
        print("\n".join(list_templates()))
        return 0
    ws = Workspace(args.dir, backend=args.llm)
    try:
        if args.ws_command == "init":
            result = ws.init(args.template, force=args.force)
        elif args.ws_command == "validate":
            result = ws.validate()
            if args.brief:
                if result["ok"]:
                    print(f"agentm2m: workspace OK ({len(result['handoffs'])} hand-off(s))")
                else:
                    print("agentm2m: workspace INVALID\n" + "\n".join(f"  - {e}" for e in result["errors"]), file=sys.stderr)
                return 0 if result["ok"] else 1
            print(json.dumps(result, indent=2))
            return 0 if result["ok"] else 1
        elif args.ws_command == "status":
            result = ws.status()
            if args.brief:
                b = result["bindings"]
                print(
                    f"agentm2m: team {result['team']} -- {len(result['agents'])} agents, "
                    f"{len(result['handoffs'])} hand-offs, bindings {b}, phi={result['phi']}"
                )
                return 0
        elif args.ws_command == "run":
            result = ws.run()
        elif args.ws_command == "impact":
            result = ws.impact()
        else:  # pragma: no cover - argparse enforces choices
            raise WorkspaceError(f"unknown command {args.ws_command}")
    except WorkspaceError as exc:
        print(f"agentm2m: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2, default=str))
    return 0


def _read_arg(value: str) -> str:
    """A file path, '-' for stdin, or the literal text."""
    if value == "-":
        return sys.stdin.read()
    p = Path(value)
    return p.read_text() if p.is_file() else value


def cmd_auto(args: argparse.Namespace) -> int:
    from .auto.workspace import AutoWorkspace, AutoWorkspaceError, check_team_json

    if args.auto_command == "check":
        rc = 0
        for t in args.team:
            res = check_team_json(_read_arg(t), naive=args.naive)
            if len(args.team) > 1:
                print(f"== {t}")
            print(res["report"])
            rc |= 0 if res["admitted"] else 1
        return rc
    ws = AutoWorkspace(args.dir, backend=args.llm)
    try:
        if args.auto_command == "task":
            result = ws.set_task(_read_arg(args.source), args.description or "")
        elif args.auto_command == "propose":
            result = ws.builder_prompt(args.mode)
            if args.prompt_only:
                print(result["prompt"])
                return 0
        elif args.auto_command == "submit":
            result = ws.submit_team(_read_arg(args.team))
            print(json.dumps(result, indent=2, default=str))
            return 0 if result.get("admitted") else 1
        elif args.auto_command == "run":
            result = ws.run()
        elif args.auto_command == "bindings":
            result = ws.next_bindings(args.agent, args.limit)
        elif args.auto_command == "fill":
            result = ws.submit_binding(args.target_key, args.binding, _read_arg(args.value), args.footprint_version)
        elif args.auto_command == "status":
            result = ws.status()
            if args.brief:
                if result.get("team") is None:
                    print(f"agentm2m auto: no admitted team ({'task set' if result.get('task') else 'no task'})")
                else:
                    run = result.get("run") or {}
                    print(f"agentm2m auto: team {result['team']['name']} -- agents {result['team']['agents']}, "
                          f"pending {run.get('pending', '?')}, phi={result.get('phi')}")
                return 0
        elif args.auto_command == "attribute":
            result = ws.attribute(args.limit)
        elif args.auto_command == "deliverable":
            result = ws.deliverable()
            if args.code:
                print(result["code"])
                return 0
        elif args.auto_command == "solve":
            result = ws.solve(_read_arg(args.source) if args.source else None)
        elif args.auto_command == "reset":
            result = ws.reset()
        else:  # pragma: no cover
            raise AutoWorkspaceError(f"unknown command {args.auto_command}")
    except AutoWorkspaceError as exc:
        print(f"agentm2m: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2, default=str))
    return 0


def cmd_serve(args: argparse.Namespace) -> int:
    try:
        from .server.app import main as serve_main
    except ImportError as exc:  # pragma: no cover
        print(f"agentm2m serve needs the 'serve' extra (pip install 'agentm2m[serve]'): {exc}", file=sys.stderr)
        return 1
    argv = ["--host", args.host, "--port", str(args.port)]
    if args.data_dir:
        argv += ["--data-dir", args.data_dir]
    if args.web_dist:
        argv += ["--web-dist", args.web_dist]
    serve_main(argv)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="agentm2m")
    sub = parser.add_subparsers(dest="command", required=True)

    p_validate = sub.add_parser("validate", help="parse-check a .agentm2m rule file")
    p_validate.add_argument("rule_file")
    p_validate.set_defaults(func=cmd_validate)

    p_trace = sub.add_parser("trace", help="trace model utilities")
    trace_sub = p_trace.add_subparsers(dest="trace_command", required=True)
    p_trace_show = trace_sub.add_parser("show", help="pretty-print a saved trace model")
    p_trace_show.add_argument("trace_file")
    p_trace_show.set_defaults(func=cmd_trace_show)

    p_llm = sub.add_parser("llm-check", help="check the configured LLM backend is reachable")
    p_llm.add_argument("--provider", default=None)
    p_llm.add_argument("--model", default=None)
    p_llm.set_defaults(func=cmd_llm_check)

    p_ws = sub.add_parser("workspace", help="manage a persistent .agentm2m/ team workspace")
    p_ws.add_argument("--dir", default=".", help="project directory containing .agentm2m/ (default: .)")
    p_ws.add_argument("--llm", default=None, help="host|mock|ollama|openai|anthropic (default: $AGENTM2M_LLM or host)")
    ws_sub = p_ws.add_subparsers(dest="ws_command", required=True)
    ws_sub.add_parser("templates", help="list built-in team templates")
    p_init = ws_sub.add_parser("init", help="create .agentm2m/ from a template")
    p_init.add_argument("template", nargs="?", default="devteam")
    p_init.add_argument("--force", action="store_true")
    for name in ("validate", "status"):
        p = ws_sub.add_parser(name)
        p.add_argument("--brief", action="store_true", help="one-line output (used by the Claude Code plugin hooks)")
    ws_sub.add_parser("run", help="run all hand-offs to a fixpoint")
    ws_sub.add_parser("impact", help="preview open obligations without any LLM call")
    p_ws.set_defaults(func=cmd_workspace)

    p_auto = sub.add_parser("auto", help="AutoM2M: checked, builder-proposed teams (.agentm2m/auto/)")
    p_auto.add_argument("--dir", default=".", help="project directory (default: .)")
    p_auto.add_argument("--llm", default=None, help="host|mock|ollama|openai|anthropic (default: $AGENTM2M_LLM or host)")
    a_sub = p_auto.add_subparsers(dest="auto_command", required=True)
    a = a_sub.add_parser("check", help="run the W1-W6 checker on typed-team JSON files")
    a.add_argument("team", nargs="+", help="JSON file(s), '-' for stdin")
    a.add_argument("--naive", action="store_true", help="class-level W4 (comparison only)")
    a = a_sub.add_parser("task", help="set the task from a Python skeleton")
    a.add_argument("source", help="Python file, '-' for stdin, or source text")
    a.add_argument("--description", default="")
    a = a_sub.add_parser("propose", help="print the builder prompt (propose / revise / delta)")
    a.add_argument("--mode", default="auto", choices=["auto", "propose", "revise", "delta"])
    a.add_argument("--prompt-only", action="store_true")
    a = a_sub.add_parser("submit", help="submit a typed team (checked; admitted or applied as a delta)")
    a.add_argument("team", help="JSON file, '-' for stdin, or JSON text")
    a_sub.add_parser("run", help="run to a fixpoint and evaluate phi")
    a = a_sub.add_parser("bindings", help="host mode: the next values to fill")
    a.add_argument("--agent", default=None)
    a.add_argument("--limit", type=int, default=5)
    a = a_sub.add_parser("fill", help="host mode: submit one value")
    a.add_argument("target_key")
    a.add_argument("binding")
    a.add_argument("value", help="file, '-' for stdin, or the value text")
    a.add_argument("footprint_version")
    a = a_sub.add_parser("status")
    a.add_argument("--brief", action="store_true")
    a = a_sub.add_parser("attribute", help="locate (and with an engine LLM classify) phi failures")
    a.add_argument("--limit", type=int, default=2)
    a = a_sub.add_parser("deliverable", help="the assembled code")
    a.add_argument("--code", action="store_true", help="print only the code")
    a = a_sub.add_parser("solve", help="the whole loop unattended (needs an engine LLM: --llm ...)")
    a.add_argument("source", nargs="?", default=None)
    a_sub.add_parser("reset", help="delete .agentm2m/auto/")
    p_auto.set_defaults(func=cmd_auto)

    p_serve = sub.add_parser("serve", help="run the hosted service: web app, REST API and MCP endpoint (/mcp)")
    p_serve.add_argument("--host", default="127.0.0.1")
    p_serve.add_argument("--port", type=int, default=8765)
    p_serve.add_argument("--data-dir", default=None)
    p_serve.add_argument("--web-dist", default=None)
    p_serve.set_defaults(func=cmd_serve)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
