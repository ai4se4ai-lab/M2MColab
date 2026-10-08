"""Host mode at the engine level (TeamRuntime + HostBackend), on the
Python-built DevTeam of examples/01_devteam: the deferred backend must yield
exactly the bindings an in-engine backend would sample, and a submitted value
must go through the same acceptance path (Algorithm 1)."""
from __future__ import annotations

import sys
from pathlib import Path

EXAMPLE_DIR = Path(__file__).resolve().parents[1] / "examples" / "01_devteam"
sys.path.insert(0, str(EXAMPLE_DIR))

from run import build_team  # noqa: E402

from agentm2m.llm.factory import make_backend  # noqa: E402
from agentm2m.llm.host_backend import HostBackend  # noqa: E402
from agentm2m.llm.mock_backend import MockBackend  # noqa: E402
from agentm2m.team.runtime import TeamRuntime  # noqa: E402

SIG = "refund(paymentId: string) -> Refund"
BODY = "def f(x):\n    return x\n"
ORACLE = "def test_oracle():\n    assert implementation() == 1\n"


def _answer(binding: str) -> str:
    return {"signature": SIG, "body": BODY, "oracle": ORACLE}[binding]


def test_factory_knows_host():
    assert isinstance(make_backend(override_provider="host"), HostBackend)


def test_host_defers_what_mock_would_sample():
    mock_rt = TeamRuntime(build_team(), MockBackend())
    sampled = {(o.target_key, o.binding) for o in mock_rt.run_to_fixpoint().obligations}

    rt = TeamRuntime(build_team(), HostBackend())
    report = rt.run_to_fixpoint()
    offered = {(p.target_key, p.binding) for p in report.pending + report.blocked}
    assert offered == sampled  # same obligations, just not sampled
    assert {p.binding for p in report.blocked} == {"body"}  # waiting on signatures
    assert not report.obligations and not rt.acceptance_holds()
    assert all("Context (footprint only)" in p.prompt for p in report.pending)
    assert all(p.prompt == "" for p in report.blocked)  # never offered with an empty footprint


def test_submit_until_fixpoint_then_phi():
    rt = TeamRuntime(build_team(), HostBackend())
    for _ in range(5):
        report = rt.run_to_fixpoint()
        if not report.pending:
            break
        for p in report.pending:
            assert rt.submit_binding(p.target_key, p.binding, _answer(p.binding))["status"] == "accepted"
    assert not report.blocked and rt.acceptance_holds()
    ops = rt.team.roots["Arch"].operations
    assert {op.signature for op in ops} == {SIG}


def test_rejections_escalate_like_the_engine_loop():
    rt = TeamRuntime(build_team(), HostBackend(), max_resamples=2)
    p = next(x for x in rt.run_to_fixpoint().pending if x.binding == "signature")
    first = rt.submit_binding(p.target_key, "signature", "no parens")
    assert first["status"] == "rejected" and first["attempts_left"] == 1
    assert rt.submit_binding(p.target_key, "signature", "still bad")["status"] == "escalated"
    # identical to the in-engine path: failed stamp -> reported, not re-offered
    report = rt.run_to_fixpoint()
    assert p.target_key not in {x.target_key for x in report.pending}
    assert any(e.target_key == p.target_key for e in report.escalations)


def test_backend_mode_is_unchanged():
    """The acceptance predicate now also requires fresh stamps; a normal
    backend run to a fixpoint must still satisfy it, and an edit must break it."""
    rt = TeamRuntime(build_team(), MockBackend())
    rt.run_to_fixpoint()
    assert rt.stamps_fresh() and rt.acceptance_holds()
    crit = next(c for s in rt.team.roots["Req"].stories for c in s.criteria if c.id == "S2.1")
    crit.text = "must refund within 2 days"
    assert not rt.stamps_fresh() and not rt.acceptance_holds()
    rt.run_to_fixpoint()
    assert rt.acceptance_holds()
