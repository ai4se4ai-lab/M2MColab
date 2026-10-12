#!/usr/bin/env python3
"""Run the ResearchTeam example: Literature-Reviewer -> Experiment-Designer
(a plain 1:1 hand-off), then Experiment-Designer + Literature-Reviewer ->
Report-Writer -- an n:m (multi-source) hand-off, docs/DS-A2A.tex Sec III-A:
"Multi-source hand-offs (n:m ...) are declared as in ATL, with several
`from` models." See README.md for how this differs from 01_devteam's
exclusively 1:1 hand-offs.

    python examples/04_research_team/run.py --llm mock       # no network needed
    python examples/04_research_team/run.py                  # local Ollama (default)
    python examples/04_research_team/run.py --llm anthropic --model claude-haiku-4-5-20251001
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from metamodels import build_experiments_mm, build_literature_mm, build_report_mm  # noqa: E402
from seed_models import build_seed_literature_model  # noqa: E402

from agenthot.config import LLMConfig  # noqa: E402
from agenthot.llm.factory import make_backend  # noqa: E402
from agenthot.team.model import Team  # noqa: E402
from agenthot.team.runtime import TeamRuntime  # noqa: E402


def build_team() -> Team:
    lit_mm = build_literature_mm()
    exp_mm = build_experiments_mm(lit_mm)
    report_mm = build_report_mm(exp_mm, lit_mm)

    lit_root = build_seed_literature_model(lit_mm)
    exp_root = exp_mm.get("ExperimentsModel")()
    report_root = report_mm.get("ReportModel")()

    team = Team()
    team.add_agent("LiteratureReviewer", "Literature")
    team.add_view(lit_mm, lit_root)
    team.add_agent("ExperimentDesigner", "Experiments")
    team.add_view(exp_mm, exp_root)
    team.add_agent("ReportWriter", "Report")
    team.add_view(report_mm, report_root)

    rules = HERE / "rules"
    team.add_handoff("Lit2Plan", rules / "Lit2Plan.agenthot", target_mm="Experiments")
    team.add_handoff("ExpLit2Report", rules / "ExpLit2Report.agenthot", target_mm="Report")
    return team


def print_models(team: Team) -> None:
    exp = team.roots["Experiments"]
    report = team.roots["Report"]

    print("\n-- Experiments (Experiment-Designer's view) --")
    for p in exp.plans:
        print(f"  ExperimentPlan {p.id} [topic={p.topic}] (claim={p.claim.id}): {p.method}")

    print("\n-- Report (Report-Writer's view -- product of the n:m hand-off) --")
    for s in report.sections:
        print(f"  ReportSection {s.id} [{s.title}] (plan={s.plan.id}, claim={s.claim.id}):")
        print(f"      {s.body}")


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

    n_claims = sum(len(p.claims) for p in team.roots["Literature"].papers)
    n_plans = len(team.roots["Experiments"].plans)
    n_sections = len(team.roots["Report"].sections)
    print(
        f"\n{n_claims} claims -> {n_plans} ExperimentPlans (1:1 hand-off) -> "
        f"{n_sections} ReportSections (n:m hand-off over plans x claims sharing a topic)."
    )
    print(
        f"n_sections ({n_sections}) > n_plans ({n_plans}): {n_sections > n_plans} "
        "-- real fan-out from the n:m match, not a disguised 1:1 pairing "
        "(the 'latency' topic alone has 2 plans x 2 claims = 4 sections)."
    )

    phi_holds = runtime.acceptance_holds()
    print(f"\nphi holds: {phi_holds}")
    return 0 if phi_holds else 1


if __name__ == "__main__":
    raise SystemExit(main())
