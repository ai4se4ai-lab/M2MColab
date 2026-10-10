"""Regression test for examples/03_security_reviewer_hot."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

EXAMPLES_DIR = Path(__file__).resolve().parents[1] / "examples"


def _load(module_name: str, path: Path):
    spec = importlib.util.spec_from_file_location(module_name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def test_security_reviewer_hot_example():
    # Load in a fresh sys.path/sys.modules context, mirroring run.py's own
    # collision-avoidance dance (see examples/03_.../run.py's docstring).
    example_dir = EXAMPLES_DIR / "03_security_reviewer_hot"
    sys.path.insert(0, str(example_dir))
    try:
        run_mod = _load("example03_run", example_dir / "run.py")
    finally:
        sys.path.remove(str(example_dir))

    from agenthot.llm.mock_backend import MockBackend
    from agenthot.team.hot import TeamChange, apply_hot
    from agenthot.team.runtime import TeamRuntime

    team = run_mod.build_team()
    llm = MockBackend()
    runtime = TeamRuntime(team, llm)
    runtime.run_to_fixpoint()
    n_ops = len(team.roots["Arch"].operations)
    assert n_ops > 0
    assert "Sec" not in team.views

    sec_mm = run_mod.build_sec_mm(team.views["Arch"])
    sec_root = sec_mm.get("SecModel")()
    apply_hot(
        team,
        TeamChange(
            agent_name="SecurityReviewer",
            view=sec_mm,
            view_root=sec_root,
            handoff_name="Arch2Sec",
            rule_path=example_dir / "rules" / "Arch2Sec.agenthot",
        ),
    )
    runtime.run_to_fixpoint()

    assert len(sec_root.reviews) == n_ops
    assert runtime.acceptance_holds()
