"""Regression test for examples/05_incident_response_team: builds the team
exactly as run.py does and checks that, with the deterministic MockBackend,
the Lift binding succeeds (RemediationAction is populated from one JSON
call) while the executable-oracle `dryRun` @check genuinely escalates
(Proposition 3), leaving phi False.

Loads run.py in isolation, evicting the plain module names it caches
afterwards -- see tests/test_example_04_research_team.py's docstring.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

EXAMPLE_DIR = Path(__file__).resolve().parents[1] / "examples" / "05_incident_response_team"


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


def test_incident_response_lift_succeeds_and_dryrun_escalates():
    build_team = _load_build_team()
    team = build_team()
    runtime = TeamRuntime(team, MockBackend(), max_resamples=1)
    report = runtime.run_to_fixpoint()

    actions = team.roots["Actions"].actions
    assert len(actions) == 2

    # Lift: the `self` binding populated name/command/risk_level from one
    # JSON-sampled call, despite the dryRun escalation below.
    for ra in actions:
        assert ra.name == "restart_service"
        assert ra.command
        assert ra.risk_level in {"low", "medium", "high"}
        # dryRun was never accepted, so it was never written.
        assert not ra.dryRun

    # The executable-oracle validator genuinely ran the sampled script (not
    # just parsed it) and rejected it every time under the mock backend, so
    # every RemediationAction has an open dryRun escalation.
    escalations = report.escalations
    assert len(escalations) == 2
    assert all(e.binding == "dryRun" for e in escalations)
    assert all(e.rule == "Incident2RemediationAction" for e in escalations)

    # Downstream hand-off still ran normally: escalation on one binding does
    # not block unrelated work.
    reports = team.roots["Reports"].reports
    assert len(reports) == 2
    assert all(pr.narrative for pr in reports)

    # phi is False by design (Incident2Action's phi requires no open
    # escalations), and the whole-team predicate reflects that.
    assert not runtime.acceptance_holds()
