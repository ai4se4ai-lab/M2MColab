"""Regression test for examples/04_research_team: builds the team exactly
as run.py does and checks the n:m hand-off's real fan-out with the
deterministic MockBackend.

examples/04, 05, and 06 each have their own same-named run.py/metamodels.py/
seed_models.py, and all example tests share one pytest process, so this
loads run.py in isolation and evicts the plain module names it caches
afterwards -- mirroring examples/03_security_reviewer_hot/run.py's own
collision-avoidance dance (see tests/test_example_03_hot.py).
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

EXAMPLE_DIR = Path(__file__).resolve().parents[1] / "examples" / "04_research_team"


def _load_build_team():
    sys.path.insert(0, str(EXAMPLE_DIR))
    for name in ("run", "metamodels", "seed_models"):
        sys.modules.pop(name, None)
    try:
        spec = importlib.util.spec_from_file_location("run", EXAMPLE_DIR / "run.py")
        module = importlib.util.module_from_spec(spec)
        sys.modules["run"] = module
        spec.loader.exec_module(module)
        build_team = module.build_team
    finally:
        for name in ("run", "metamodels", "seed_models"):
            sys.modules.pop(name, None)
        while str(EXAMPLE_DIR) in sys.path:
            sys.path.remove(str(EXAMPLE_DIR))
    return build_team


from agenthot.llm.mock_backend import MockBackend  # noqa: E402
from agenthot.team.runtime import TeamRuntime  # noqa: E402


def test_research_team_nm_handoff_fans_out_and_reaches_acceptance():
    build_team = _load_build_team()
    team = build_team()
    runtime = TeamRuntime(team, MockBackend())
    runtime.run_to_fixpoint()

    assert runtime.acceptance_holds()

    # 4 claims -> 4 ExperimentPlans via the 1:1 Lit2Plan hand-off.
    plans = team.roots["Experiments"].plans
    assert len(plans) == 4

    # The n:m hand-off matches every (plan, claim) pair sharing a topic.
    # The seed model gives topic "latency" to two claims (C2, C4) from two
    # different papers, so that topic alone yields 2 plans x 2 claims = 4
    # sections; "hallucination" and "coordination" each yield 1 -> 6 total.
    sections = team.roots["Report"].sections
    assert len(sections) == 6
    assert len(sections) > len(plans)  # genuine fan-out, not a disguised 1:1

    latency_sections = [s for s in sections if s.title == "latency"]
    assert len(latency_sections) == 4
    pairs = {(s.plan.id, s.claim.id) for s in latency_sections}
    assert pairs == {("C2", "C2"), ("C2", "C4"), ("C4", "C2"), ("C4", "C4")}

    # Every section carries structural references into BOTH source views.
    for s in sections:
        assert s.plan is not None
        assert s.claim is not None
        assert s.plan.topic == s.claim.topic
