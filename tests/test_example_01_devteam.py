"""Regression test for examples/01_devteam: builds the team exactly as
run.py does and checks phi holds with the deterministic MockBackend."""
from __future__ import annotations

import sys
from pathlib import Path

EXAMPLE_DIR = Path(__file__).resolve().parents[1] / "examples" / "01_devteam"
sys.path.insert(0, str(EXAMPLE_DIR))

from run import build_team  # noqa: E402

from agentm2m.llm.mock_backend import MockBackend
from agentm2m.team.runtime import TeamRuntime


def test_devteam_reaches_acceptance_with_mock_backend():
    team = build_team()
    runtime = TeamRuntime(team, MockBackend())
    runtime.run_to_fixpoint()

    assert runtime.acceptance_holds()
    # S1, S2 accepted -> 2 operations; S3 stays a draft -> guarded out (Prop. 1).
    assert len(team.roots["Arch"].operations) == 2
    assert len(team.roots["Code"].edits) == 2
    # All 4 criteria (across all stories, regardless of story status) get a
    # test case: T4 matches Req!Criterion directly, independent of T1's guard.
    assert len(team.roots["Test"].cases) == 4
