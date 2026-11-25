#!/usr/bin/env python3
"""Run the DevTeam example end to end: Analyst -> Architect -> Developer,
plus the direct Analyst -> Tester hand-off (Fig. 1 / Sec III-B).

    python examples/01_devteam/run.py                 # local Ollama (default)
    python examples/01_devteam/run.py --llm mock       # no network/API key
    python examples/01_devteam/run.py --llm anthropic --model claude-haiku-4-5-20251001
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from metamodels import build_arch_mm, build_code_mm, build_req_mm, build_test_mm  # noqa: E402
from seed_models import build_seed_req_model  # noqa: E402

from agenthot.config import LLMConfig  # noqa: E402
from agenthot.llm.factory import make_backend  # noqa: E402
from agenthot.team.model import Team  # noqa: E402
from agenthot.team.runtime import TeamRuntime  # noqa: E402


def build_team() -> Team:
    req_mm = build_req_mm()
    arch_mm = build_arch_mm()
    code_mm = build_code_mm(arch_mm)
    test_mm = build_test_mm(req_mm)

    req_root = build_seed_req_model(req_mm)
    arch_root = arch_mm.get("ArchModel")()
    code_root = code_mm.get("CodeModel")()
    test_root = test_mm.get("TestModel")()

    team = Team()
    team.add_agent("Analyst", "Req")
    team.add_view(req_mm, req_root)
    team.add_agent("Architect", "Arch")
    team.add_view(arch_mm, arch_root)
    team.add_agent("Developer", "Code")
    team.add_view(code_mm, code_root)
    team.add_agent("Tester", "Test")
    team.add_view(test_mm, test_root)

    rules = HERE / "rules"
    team.add_handoff("Req2Arch", rules / "Req2Arch.agenthot", target_mm="Arch")
    team.add_handoff("Arch2Code", rules / "Arch2Code.agenthot", target_mm="Code")
    team.add_handoff("Req2Test", rules / "Req2Test.agenthot", target_mm="Test")
    return team


def print_models(team: Team) -> None:
    arch = team.roots["Arch"]
    code = team.roots["Code"]
    test = team.roots["Test"]

    print("\n-- Arch (Architect's view) --")
    for op in arch.operations:
        print(f"  Operation {op.name}: {op.signature}  [component={op.component.name if op.component else None}]")

    print("\n-- Code (Developer's view) --")
    for ce in code.edits:
        print(f"  CodeEdit {ce.name} (operation={ce.operation.name}):")
        if ce.body is None:
            print("      <no body: @llm binding escalated -- see ESCALATIONS above>")
            continue
        for line in ce.body.splitlines():
            print(f"      {line}")

    print("\n-- Test (Tester's view) --")
    for tc in test.cases:
        print(f"  TestCase for criterion {tc.criterion.text!r}:")
        if tc.oracle is None:
            print("      <no oracle: @llm binding escalated -- see ESCALATIONS above>")
            continue
        for line in tc.oracle.splitlines():
            print(f"      {line}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--llm", default=None, help="ollama|openai|anthropic|mock (default: $LLM_PROVIDER, else ollama)")
    parser.add_argument("--model", default=None, help="model tag override")
    args = parser.parse_args()

    team = build_team()
    cfg = LLMConfig.from_env()
    llm = make_backend(cfg, override_provider=args.llm, override_model=args.model)
    print(f"Using LLM backend: {llm.name} ({args.model or cfg.model})")

    runtime = TeamRuntime(team, llm)
    report = runtime.run_to_fixpoint()

    print(report.summary())
    print_models(team)

    phi_holds = runtime.acceptance_holds()
    print(f"\nAcceptance predicate phi holds: {phi_holds}")
    return 0 if phi_holds else 1


if __name__ == "__main__":
    raise SystemExit(main())
