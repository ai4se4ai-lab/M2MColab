"""Change propagation through persisted trace models, across processes.

The paper's Step 6: tightening S2.1 obliges exactly the bindings whose
footprint reads it -- Obl = {(op_S2, signature), (tc_S2.1, oracle),
(ed_S2, body)}, the code edit directly because T2 also reads the criteria T1
copies onto the operation -- while S1's artifacts are untouched, and a
re-derived value obliges further bindings only if it actually changed. These run with the
workspace reloaded from disk (and, in one test, in separate OS processes),
which exercises the JSON store that replaced in-process-only state.
"""
from __future__ import annotations

import json
import subprocess
import sys

from conftest import GOOD_SIGNATURE, REPO, fill_all

from agentm2m.workspace import Workspace

TIGHTEN_S21 = [{"op": "set", "key": "Criterion#S2.1", "values": {"text": "completing a done task returns HTTP 409"}}]


def _reload(ws: Workspace) -> Workspace:
    return Workspace(ws.project_dir, backend="host", max_resamples=3)


def test_state_survives_reload_and_rerun_is_a_noop(host_ws: Workspace):
    fill_all(host_ws)
    ws2 = _reload(host_ws)
    assert ws2.acceptance()["phi"] is True
    r = ws2.run()
    assert r["pending"] == 0 and r["blocked"] == 0
    assert all(h["created"] == 0 and h["deleted"] == 0 for h in r["handoffs"].values())
    # cross-view references and engine ownership survived the round trip
    edit = ws2.show("Code", "CodeEdit#op_s2")["element"]
    assert edit["operation"] == "Arch:Operation#op_s2"
    assert edit["engine_owned"].startswith("Operation2CodeEdit::")
    tc = ws2.show("Test", "TestCase#S2.1")["element"]
    assert tc["criterion"] == "Req:Criterion#S2.1"


def test_impact_is_exactly_the_affected_bindings(host_ws: Workspace):
    fill_all(host_ws)
    ws = _reload(host_ws)
    preview = ws.impact("Req", TIGHTEN_S21, "Analyst")
    got = {(o["target_key"], o["binding"]) for o in preview["obligations"]}
    assert got == {
        ("Story2Operation::op::s=UserStory#S2", "signature"),
        ("Criterion2TestCase::tc::c=Criterion#S2.1", "oracle"),
        ("Operation2CodeEdit::ce::op=Operation#op_s2", "body"),
    }
    assert {o["agent"] for o in preview["obligations"]} == {"Architect", "Tester", "Developer"}
    assert preview["new_bindings"] == [] and preview["created"] == [] and preview["deleted"] == []
    # impact is a pure preview: nothing changed on disk or in memory
    assert ws.show("Req", "Criterion#S2.1")["element"]["text"] == "completing a done task is rejected"
    assert ws.acceptance()["phi"] is True


def test_identical_rederivation_stops_propagation(host_ws: Workspace):
    fill_all(host_ws)
    ws = _reload(host_ws)
    ws.edit("Req", TIGHTEN_S21, "Analyst")
    first = ws.next_bindings(limit=50)["bindings"]
    assert {(b["target_key"], b["kind"]) for b in first} == {
        ("Story2Operation::op::s=UserStory#S2", "stale"),
        ("Criterion2TestCase::tc::c=Criterion#S2.1", "stale"),
        ("Operation2CodeEdit::ce::op=Operation#op_s2", "stale"),
    }
    # Same signature again (a 409 need not change it): each obligation is
    # discharged exactly once -- nothing downstream is obliged a second time.
    results = fill_all(ws)
    assert len(results) == 3 and all(r["status"] == "accepted" for r in results)
    assert ws.acceptance()["phi"] is True


def test_changed_rederivation_propagates_one_hop_further(host_ws: Workspace):
    fill_all(host_ws)
    ws = _reload(host_ws)
    ws.edit("Req", TIGHTEN_S21, "Analyst")

    def answer(b: dict) -> str:
        if b["binding"] == "signature":
            return "markTaskDone(id: TaskId) -> TaskOrConflict"
        from conftest import good_value

        return good_value(b)

    results = fill_all(ws, answer)
    body_key = ("Operation2CodeEdit::ce::op=Operation#op_s2", "body")
    accepted = [(r["target_key"], r["binding"]) for r in results if r["status"] == "accepted"]
    # The body was offered with the old signature in the same batch; once the
    # changed signature is accepted, that answer no longer matches its
    # footprint and is refused as stale, then re-offered with the new one.
    assert [r["status"] for r in results if (r["target_key"], r["binding"]) == body_key] == ["stale", "accepted"]
    assert sorted(accepted) == sorted([
        ("Story2Operation::op::s=UserStory#S2", "signature"),
        ("Criterion2TestCase::tc::c=Criterion#S2.1", "oracle"),
        body_key,
    ])
    assert not any("S1" in t or "op_s1" in t for t, _ in accepted)  # S1 untouched
    assert ws.acceptance()["phi"] is True
    assert ws.acceptance()["phi"] is True


def test_add_and_remove_criteria(host_ws: Workspace):
    fill_all(host_ws)
    ws = _reload(host_ws)
    add = [{"op": "create", "parent": "UserStory#S1", "feature": "criteria", "value": {"id": "S1.2", "text": "titles are trimmed"}}]
    preview = ws.impact("Req", add, "Analyst")
    assert preview["created"] == ["Criterion2TestCase::tc::c=Criterion#S1.2"]
    # the new criterion also changes S1's signature footprint (s.criteria)
    assert {(o["target_key"], o["binding"]) for o in preview["obligations"]} == {
        ("Story2Operation::op::s=UserStory#S1", "signature"),
        ("Operation2CodeEdit::ce::op=Operation#op_s1", "body"),
    }
    assert {(n["target_key"], n["binding"]) for n in preview["new_bindings"]} == {("Criterion2TestCase::tc::c=Criterion#S1.2", "oracle")}

    remove = [{"op": "delete", "key": "UserStory#S2"}]
    preview = ws.impact("Req", remove, "Analyst")
    assert set(preview["deleted"]) == {
        "Story2Operation::op::s=UserStory#S2",
        "Operation2CodeEdit::ce::op=Operation#op_s2",
        "Criterion2TestCase::tc::c=Criterion#S2.1",
    }
    assert preview["obligations"] == [] and preview["llm_calls_upper_bound"] == 0  # removal is purely structural
    ws.edit("Req", remove, "Analyst")
    r = ws.run()
    assert r["pending"] == 0 and r["phi"] is True
    assert [e["name"] for e in ws.show("Code")["model"]["edits"]] == ["op_s1"]


def _cli(project, *args):
    return subprocess.run(
        [sys.executable, "-m", "agentm2m.cli", "workspace", "--dir", str(project), *args],
        capture_output=True, text=True, cwd=REPO, env={"PYTHONPATH": str(REPO / "src"), "PATH": "/usr/bin:/bin"},
    )


def test_cross_process_workflow(project):
    # process 1: create + run with the deterministic mock backend
    assert _cli(project, "--llm", "mock", "init", "devteam").returncode == 0
    r = _cli(project, "--llm", "mock", "run")
    assert r.returncode == 0, r.stderr
    assert json.loads(r.stdout)["phi"] is True
    # process 2 (in-test): edit the requirement
    ws = Workspace(project, backend="host")
    ws.edit("Req", TIGHTEN_S21, "Analyst")
    # process 3: impact computed from the persisted traces alone
    r = _cli(project, "impact")
    assert r.returncode == 0, r.stderr
    got = {(o["target_key"], o["binding"]) for o in json.loads(r.stdout)["obligations"]}
    assert got == {
        ("Story2Operation::op::s=UserStory#S2", "signature"),
        ("Criterion2TestCase::tc::c=Criterion#S2.1", "oracle"),
        ("Operation2CodeEdit::ce::op=Operation#op_s2", "body"),
    }
    r = _cli(project, "status", "--brief")
    assert r.returncode == 0 and "phi=False" in r.stdout and "'stale': 3" in r.stdout


def test_concurrent_instances_see_each_others_writes(host_ws: Workspace):
    other = _reload(host_ws)
    b = host_ws.next_bindings(agent="Architect", limit=1)["bindings"][0]
    assert host_ws.submit_binding(b["target_key"], "signature", GOOD_SIGNATURE)["status"] == "accepted"
    # `other` loaded before the write; it must reload rather than overwrite it
    assert other.submit_binding(b["target_key"], "signature", GOOD_SIGNATURE)["status"] == "already_accepted"
