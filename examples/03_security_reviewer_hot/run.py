#!/usr/bin/env python3
"""Runtime team evolution via a higher-order transformation (Sec III-D):
starts from examples/01_devteam's Analyst/Architect/Developer/Tester team,
runs it, then *while the process is running* adds a Security Reviewer
related to Operation -- Fig. 1's dashed edge -- and shows it receives
obligations for every pre-existing Operation, with no hand-written glue.

    python examples/03_security_reviewer_hot/run.py --llm mock
"""
from __future__ import annotations

import argparse
import importlib.util
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
DEVTEAM_DIR = HERE.parents[0] / "01_devteam"
sys.path.insert(0, str(HERE))


def _load_devteam_build_team():
    # examples/01_devteam/run.py does plain `from metamodels import ...` /
    # `from seed_models import ...` internally, so it needs DEVTEAM_DIR on
    # sys.path (and no same-named module already cached) while it executes.
    # This example has its own same-named metamodels.py, so we load 01's
    # run.py in isolation and evict the names it cached afterwards, rather
    # than leaving both directories on sys.path at once.
    sys.path.insert(0, str(DEVTEAM_DIR))
    spec = importlib.util.spec_from_file_location("run", DEVTEAM_DIR / "run.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules["run"] = module
    spec.loader.exec_module(module)
    build_team = module.build_team
    for name in ("run", "metamodels", "seed_models"):
        sys.modules.pop(name, None)
    # 01/run.py's own top level also inserts DEVTEAM_DIR onto sys.path;
    # remove every occurrence, not just the one we added.
    while str(DEVTEAM_DIR) in sys.path:
        sys.path.remove(str(DEVTEAM_DIR))
    return build_team


build_team = _load_devteam_build_team()

from metamodels import build_sec_mm  # noqa: E402  (this example's OWN metamodels.py)

from agentm2m.config import LLMConfig  # noqa: E402
from agentm2m.llm.factory import make_backend  # noqa: E402
from agentm2m.team.hot import TeamChange, apply_hot  # noqa: E402
from agentm2m.team.runtime import TeamRuntime  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--llm", default=None)
    parser.add_argument("--model", default=None)
    args = parser.parse_args()

    team = build_team()
    cfg = LLMConfig.from_env()
    llm = make_backend(cfg, override_provider=args.llm, override_model=args.model)
    print(f"Using LLM backend: {llm.name} ({args.model or cfg.model})")

    runtime = TeamRuntime(team, llm)

    print("=== Team before evolution: Analyst, Architect, Developer, Tester ===")
    print(sorted(team.agents))
    runtime.run_to_fixpoint()
    n_ops = len(team.roots["Arch"].operations)
    print(f"{n_ops} operations already exist in the Architect's view.\n")

    print("=== HOT: add a Security Reviewer related to Operation ===")
    sec_mm = build_sec_mm(team.views["Arch"])
    sec_root = sec_mm.get("SecModel")()
    apply_hot(
        team,
        TeamChange(
            agent_name="SecurityReviewer",
            view=sec_mm,
            view_root=sec_root,
            handoff_name="Arch2Sec",
            rule_path=HERE / "rules" / "Arch2Sec.agentm2m",
        ),
    )
    print(f"Team after evolution: {sorted(team.agents)}\n")

    report = runtime.run_to_fixpoint()
    print(report.summary())

    print("\n-- Sec (Security Reviewer's view) --")
    for sr in team.roots["Sec"].reviews:
        print(f"  Review of {sr.operation.name} [{sr.risk}]: {sr.notes}")

    retroactive = len(team.roots["Sec"].reviews) == n_ops
    print(f"\nEvery pre-existing Operation got a retroactive review, no hand-written glue: {retroactive}")
    print(f"Acceptance predicate phi holds (whole team, incl. the new hand-off): {runtime.acceptance_holds()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
