"""autom2m CLI: the AgentHOT runtime commands (validate, trace, llm-check,
workspace) plus the team level: `auto` (lift, propose, check, run, attribute,
repair a typed team) and `serve` (web app, REST API and MCP endpoint).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from agenthot.cli import build_parser as _agenthot_parser


def _read_arg(value: str) -> str:
    """A file path, '-' for stdin, or the literal text."""
    if value == "-":
        return sys.stdin.read()
    p = Path(value)
    return p.read_text() if p.is_file() else value


def cmd_auto(args: argparse.Namespace) -> int:
    from .workspace import AutoWorkspace, AutoWorkspaceError, check_team_json

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
                    print(f"autom2m auto: no admitted team ({'task set' if result.get('task') else 'no task'})")
                else:
                    run = result.get("run") or {}
                    print(f"autom2m auto: team {result['team']['name']} -- agents {result['team']['agents']}, "
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
        print(f"autom2m: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2, default=str))
    return 0


def cmd_serve(args: argparse.Namespace) -> int:
    try:
        from .server.app import main as serve_main
    except ImportError as exc:  # pragma: no cover
        print(f"autom2m serve needs the 'serve' extra (pip install 'autom2m[serve]'): {exc}", file=sys.stderr)
        return 1
    argv = ["--host", args.host, "--port", str(args.port)]
    if args.data_dir:
        argv += ["--data-dir", args.data_dir]
    if args.web_dist:
        argv += ["--web-dist", args.web_dist]
    serve_main(argv)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = _agenthot_parser()
    parser.prog = "autom2m"
    sub = next(a for a in parser._actions if isinstance(a, argparse._SubParsersAction))
    p_auto = sub.add_parser("auto", help="AutoM2M: checked, builder-proposed teams (.autom2m/)")
    p_auto.add_argument("--dir", default=".", help="project directory (default: .)")
    p_auto.add_argument("--llm", default=None, help="host|mock|ollama|openai|anthropic (default: $AGENTHOT_LLM or host)")
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
    a_sub.add_parser("reset", help="delete .autom2m/")
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
