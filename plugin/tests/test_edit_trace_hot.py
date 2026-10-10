"""Edits under write rights (omega), trace queries, and runtime team
evolution (HOT) through the workspace API."""
from __future__ import annotations

import pytest
import yaml
from conftest import fill_all

from agenthot.workspace import Workspace, WorkspaceError

SEC = dict(agent="SecurityReviewer", view="Sec", handoff="Arch2Sec", rule="rules/extra/Arch2Sec.agenthot")


def _sec_spec(ws: Workspace) -> dict:
    return yaml.safe_load((ws.dir / "rules/extra/SecurityReviewer.view.yaml").read_text())


# -- omega and engine ownership -------------------------------------------------

def test_only_the_owner_may_write_a_view(host_ws: Workspace):
    with pytest.raises(WorkspaceError, match="write rights"):
        host_ws.edit("Req", [{"op": "set", "key": "Criterion#S1.1", "values": {"text": "x"}}], "Architect")
    with pytest.raises(WorkspaceError, match="unknown agent"):
        host_ws.edit("Req", [{"op": "set", "key": "Criterion#S1.1", "values": {"text": "x"}}], "Mallory")


def test_engine_owned_elements_are_read_only(host_ws: Workspace):
    host_ws.run()
    with pytest.raises(WorkspaceError, match="engine-owned"):
        host_ws.edit("Arch", [{"op": "set", "key": "Operation#op_s1", "values": {"signature": "x() -> Y"}}], "Architect")
    with pytest.raises(WorkspaceError, match="engine-owned"):
        host_ws.edit("Arch", [{"op": "delete", "key": "Operation#op_s2"}], "Architect")


def test_owner_may_add_its_own_structure(host_ws: Workspace):
    host_ws.run()
    r = host_ws.edit("Arch", [{"op": "create", "feature": "components", "value": {"name": "Store"}}], "Architect")
    assert r["applied"] == ["create Component in components"]
    comps = [c["name"] for c in host_ws.show("Arch")["model"]["components"]]
    assert comps == ["E1", "Store"]
    host_ws.run()  # lifted element survives re-runs (not a hand-off target)
    assert [c["name"] for c in host_ws.show("Arch")["model"]["components"]] == ["E1", "Store"]


def test_edits_are_atomic(host_ws: Workspace):
    ops = [
        {"op": "set", "key": "Criterion#S1.1", "values": {"text": "changed"}},
        {"op": "set", "key": "Criterion#NOPE", "values": {"text": "x"}},
    ]
    with pytest.raises(WorkspaceError, match="no element"):
        host_ws.edit("Req", ops, "Analyst")
    assert host_ws.show("Req", "Criterion#S1.1")["element"]["text"] == "a title is required"
    assert Workspace(host_ws.project_dir).show("Req", "Criterion#S1.1")["element"]["text"] == "a title is required"


@pytest.mark.parametrize("op,err", [
    ({"op": "set", "key": "UserStory#S1", "values": {"colour": "red"}}, "no feature"),
    ({"op": "set", "key": "UserStory#S1", "values": {"epic": "Epic#E9"}}, "no element"),
    ({"op": "create", "feature": "nope", "value": {}}, "not a containment"),
    ({"op": "create", "value": {}}, "needs 'feature'"),
    ({"op": "rename", "key": "UserStory#S1"}, "unknown op"),
    ({"op": "set", "key": "UserStory#S1"}, "non-empty 'values'"),
])
def test_bad_ops_are_explained(host_ws: Workspace, op, err):
    with pytest.raises(WorkspaceError, match=err):
        host_ws.edit("Req", [op], "Analyst")


def test_unkeyed_matched_element_is_rejected(host_ws: Workspace):
    # a story without an id can't be trace-keyed -> the hand-off could not match it
    with pytest.raises(WorkspaceError, match="can no longer match"):
        host_ws.edit("Req", [{"op": "create", "feature": "stories", "value": {"status": "accepted", "epic": "Epic#E1"}}], "Analyst")


def test_new_story_flows_through_the_team(host_ws: Workspace):
    fill_all(host_ws)
    host_ws.edit("Req", [{"op": "create", "feature": "stories", "value": {
        "id": "S4", "title": "delete a task", "status": "accepted", "epic": "Epic#E1",
        "criteria": [{"id": "S4.1", "text": "deleting a missing task returns 404"}]}}], "Analyst")
    results = fill_all(host_ws)
    assert sorted((r["target_key"], r["binding"]) for r in results) == [
        ("Criterion2TestCase::tc::c=Criterion#S4.1", "oracle"),
        ("Operation2CodeEdit::ce::op=Operation#op_s4", "body"),
        ("Story2Operation::op::s=UserStory#S4", "signature"),
    ]
    assert host_ws.acceptance()["phi"] is True


def test_accepting_a_draft_story_hands_it_off(host_ws: Workspace):
    fill_all(host_ws)
    preview = host_ws.impact("Req", [{"op": "set", "key": "UserStory#S3", "values": {"status": "accepted"}}], "Analyst")
    assert "Story2Operation::op::s=UserStory#S3" in preview["created"]
    assert "Operation2CodeEdit::ce::op=Operation#op_s3" in preview["created"]


# -- traces -------------------------------------------------------------------

def test_trace_query_downstream_closure_and_upstream(host_ws: Workspace):
    fill_all(host_ws)
    down = host_ws.trace_query("UserStory#S2")
    targets = {d["target"] for d in down["downstream"]}
    assert targets == {"Arch:Operation#op_s2", "Code:CodeEdit#op_s2"}  # transitive through T2
    assert host_ws.trace_query("UserStory#S2", transitive=False)["downstream"][0]["target"] == "Arch:Operation#op_s2"
    up = host_ws.trace_query("Code:CodeEdit#op_s2")["upstream"]
    assert up == [{"handoff": "Arch2Code", "rule": "Operation2CodeEdit", "sources": {"op": "Operation#op_s2"}}]
    crit = host_ws.trace_query("Criterion#S2.1")["downstream"]
    assert [d["target"] for d in crit] == ["Test:TestCase#S2.1"]
    assert crit[0]["accepted_bindings"] == ["oracle"]


# -- HOT --------------------------------------------------------------------------

def test_evolve_adds_reviewer_with_retroactive_obligations(host_ws: Workspace):
    fill_all(host_ws)
    r = host_ws.evolve(**SEC, view_spec=_sec_spec(host_ws))
    assert r["existing_matches"] == 2
    status = host_ws.status()
    assert status["agents"]["SecurityReviewer"] == ["Sec"]
    assert status["phi"] is False  # phi strengthened: the new hand-off must hold too
    batch = host_ws.next_bindings(agent="SecurityReviewer", limit=10)["bindings"]
    assert sorted((b["target_key"], b["binding"]) for b in batch) == [
        ("Operation2Review::sr::op=Operation#op_s1", "notes"),
        ("Operation2Review::sr::op=Operation#op_s1", "risk"),
        ("Operation2Review::sr::op=Operation#op_s2", "notes"),
        ("Operation2Review::sr::op=Operation#op_s2", "risk"),
    ]
    # only the reviewer got work: nothing of the original team is re-derived
    assert all(b["agent"] == "SecurityReviewer" for b in host_ws.next_bindings(limit=50)["bindings"])
    fill_all(host_ws)
    assert host_ws.acceptance()["phi"] is True


def test_evolution_persists_and_joins_change_propagation(host_ws: Workspace):
    fill_all(host_ws)
    host_ws.evolve(**SEC, view_spec=_sec_spec(host_ws))
    fill_all(host_ws)
    ws2 = Workspace(host_ws.project_dir, backend="host")
    st = ws2.status()
    assert st["evolutions"] == ["SecurityReviewer"] and st["phi"] is True
    assert [r["risk"] for r in ws2.show("Sec")["model"]["reviews"]] == ["medium", "medium"]
    # a changed signature now also obliges the reviewer (hop through T5)
    ws2.edit("Req", [{"op": "set", "key": "Criterion#S2.1", "values": {"text": "returns 409"}}], "Analyst")
    results = fill_all(ws2, lambda b: {"signature": "done(id: TaskId) -> Task"}.get(b["binding"]) or __import__("conftest").good_value(b))
    touched = {(r["target_key"], r["binding"]) for r in results}
    assert ("Operation2Review::sr::op=Operation#op_s2", "risk") in touched
    assert not any("op_s1" in t for t, _ in touched)


def test_evolve_with_rule_text_and_rollback_on_error(host_ws: Workspace):
    bad = "module Broken; create OUT : Sec from IN : Arch; rule R { from"
    with pytest.raises(WorkspaceError, match="does not parse"):
        host_ws.evolve(agent="X", view="Sec", view_spec=_sec_spec(host_ws), handoff="Arch2Sec2", rule="rules/Sec2.agenthot", rule_text=bad)
    assert not (host_ws.dir / "rules/Sec2.agenthot").exists()
    assert "X" not in host_ws.status()["agents"]

    wrong_target = (host_ws.dir / "rules/extra/Arch2Sec.agenthot").read_text()
    with pytest.raises(WorkspaceError, match="expected the new view 'Audit'"):
        host_ws.evolve(agent="Auditor", view="Audit", view_spec=_sec_spec(host_ws), handoff="A2", rule="rules/A2.agenthot", rule_text=wrong_target)
    with pytest.raises(WorkspaceError, match="escapes"):
        host_ws.evolve(**{**SEC, "rule": "../../evil.agenthot"}, view_spec=_sec_spec(host_ws))
    with pytest.raises(WorkspaceError, match="already exists"):
        host_ws.evolve(**{**SEC, "view": "Arch"}, view_spec=_sec_spec(host_ws))

    text = wrong_target.replace("uses '../helpers.py';", "uses 'helpers.py';")
    r = host_ws.evolve(**{**SEC, "rule": "rules/Arch2Sec.agenthot"}, view_spec=_sec_spec(host_ws), rule_text=text)
    assert r["handoff"] == "Arch2Sec" and (host_ws.dir / "rules/Arch2Sec.agenthot").is_file()
