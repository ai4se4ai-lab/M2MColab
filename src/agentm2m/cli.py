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

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
