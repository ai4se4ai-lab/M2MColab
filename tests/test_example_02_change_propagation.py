"""Regression test for examples/02_devteam_change_propagation: obligations
after an edit must be a non-empty, strict subset of the initial run's."""
from __future__ import annotations

import sys
from pathlib import Path

EXAMPLE_DIR = Path(__file__).resolve().parents[1] / "examples" / "01_devteam"
sys.path.insert(0, str(EXAMPLE_DIR))

from run import build_team  # noqa: E402

from agenthot.llm.mock_backend import MockBackend
from agenthot.team.runtime import TeamRuntime


def test_change_propagation_is_localized():
    team = build_team()
    runtime = TeamRuntime(team, MockBackend())
    report1 = runtime.run_to_fixpoint()
    assert len(report1.obligations) == 8  # 2 signatures + 2 bodies + 4 oracles

    crit = next(c for s in team.roots["Req"].stories for c in s.criteria if c.id == "S2.1")
    crit.text = "must refund within 2 business days AND notify the customer by email"

    report2 = runtime.run_to_fixpoint()
    touched = {(o.handoff, o.target_key) for o in report2.obligations}

    assert touched, "the edit must raise at least one obligation"
    assert len(touched) < len(report1.obligations)
    assert all("S2" in target for _, target in touched)
