"""Host mode: Claude Code fills @llm bindings through the workspace API.

Covers the whole lifecycle the /autom2m:run skill drives: structure first,
footprint-only prompts, blocked-on-upstream bindings, validator feedback,
escalation after k rejections, and the engine-decided acceptance predicate.
"""
from __future__ import annotations

from conftest import GOOD_BODY, GOOD_ORACLE, GOOD_SIGNATURE, fill_all

from agenthot.workspace import Workspace


def test_first_run_builds_structure_without_values(host_ws: Workspace):
    r = host_ws.run()
    # T1: 1 component + 2 accepted stories (S3 is a draft: guard excludes it).
    assert r["handoffs"]["Req2Arch"]["created"] == 3
    # T2 reads operations; T4 creates one test case per criterion (all 3).
    assert r["handoffs"]["Arch2Code"]["created"] == 2
    assert r["handoffs"]["Req2Test"]["created"] == 3
    # 2 signatures + 3 oracles ready; 2 code bodies wait on the signatures.
    assert r["pending"] == 5
    assert r["blocked"] == 2
    assert r["phi"] is False
    arch = host_ws.show("Arch")["model"]
    names = sorted(op["name"] for op in arch["operations"])
    assert names == ["op_s1", "op_s2"]
    assert all("signature" not in op for op in arch["operations"])  # nothing sampled


def test_prompt_contains_only_the_footprint(host_ws: Workspace):
    batch = host_ws.next_bindings(agent="Architect", limit=10)["bindings"]
    s2 = next(b for b in batch if b["target_key"].endswith("UserStory#S2"))
    assert "completing a done task is rejected" in s2["prompt"]  # S2's own criterion
    assert "a title is required" not in s2["prompt"]  # S1's criterion: not in the footprint
    assert "export tasks" not in s2["prompt"]
    assert s2["agent"] == "Architect" and s2["kind"] == "new"


def test_agent_filter(host_ws: Workspace):
    tester = host_ws.next_bindings(agent="Tester", limit=10)["bindings"]
    assert {b["binding"] for b in tester} == {"oracle"} and len(tester) == 3
    assert host_ws.next_bindings(agent="Developer", limit=10)["bindings"] == []  # blocked


def test_full_loop_reaches_phi(host_ws: Workspace):
    results = fill_all(host_ws)
    assert [r["status"] for r in results].count("accepted") == 7
    acc = host_ws.acceptance()
    assert acc["phi"] is True and acc["open_total"] == 0
    code = host_ws.show("Code")["model"]["edits"]
    assert all(e["body"] == GOOD_BODY for e in code)
    status = host_ws.status()
    assert status["bindings"] == {"fresh": 7}
    assert status["phi"] is True


def test_blocked_bindings_unblock_after_upstream_accepted(host_ws: Workspace):
    for b in host_ws.next_bindings(agent="Architect", limit=10)["bindings"]:
        assert host_ws.submit_binding(b["target_key"], b["binding"], GOOD_SIGNATURE)["status"] == "accepted"
    dev = host_ws.next_bindings(agent="Developer", limit=10)["bindings"]
    assert len(dev) == 2
    assert all(GOOD_SIGNATURE in b["prompt"] for b in dev)  # footprint = op.signature


def test_rejection_gives_reason_and_retry_prompt(host_ws: Workspace):
    b = host_ws.next_bindings(agent="Architect", limit=1)["bindings"][0]
    r = host_ws.submit_binding(b["target_key"], "signature", "markTaskDone()")  # no params -> rejected
    assert r["status"] == "rejected"
    assert r["attempts"] == 1 and r["attempts_left"] == 2
    assert "markTaskDone()" in r["retry_prompt"]
    # The next offer of the same binding carries the feedback too.
    again = next(x for x in host_ws.next_bindings(agent="Architect", limit=10)["bindings"] if x["target_key"] == b["target_key"])
    assert again["attempts"] == 1 and "previous answer was rejected" in again["prompt"]
    # A rejected value is never written to the model.
    assert "signature" not in host_ws.show("Arch", "Operation#op_s1")["element"]
    assert host_ws.submit_binding(b["target_key"], "signature", GOOD_SIGNATURE)["status"] == "accepted"


def test_validator_reason_reaches_the_host(host_ws: Workspace):
    b = next(x for x in host_ws.next_bindings(agent="Tester", limit=10)["bindings"])
    vacuous = "def test_oracle():\n    assert True\n"
    r = host_ws.submit_binding(b["target_key"], "oracle", vacuous)
    assert r["status"] == "rejected"
    assert "passed against an unimplemented stub" in r["reason"]
    assert host_ws.submit_binding(b["target_key"], "oracle", GOOD_ORACLE)["status"] == "accepted"


def test_escalates_after_k_rejections_and_is_not_reoffered(host_ws: Workspace):
    b = host_ws.next_bindings(agent="Architect", limit=1)["bindings"][0]
    statuses = [host_ws.submit_binding(b["target_key"], "signature", "nope")["status"] for _ in range(3)]
    assert statuses == ["rejected", "rejected", "escalated"]
    assert host_ws.submit_binding(b["target_key"], "signature", GOOD_SIGNATURE)["status"] == "escalated"
    offered = {x["target_key"] for x in host_ws.next_bindings(limit=50)["bindings"]}
    assert b["target_key"] not in offered
    r = host_ws.run()
    assert any(e["target_key"] == b["target_key"] for e in r["escalations"])
    fill_all(host_ws)
    acc = host_ws.acceptance()
    assert acc["phi"] is False
    states = {(o["target_key"], o["binding"]): o["state"] for o in acc["open"]}
    assert states[(b["target_key"], "signature")] == "escalated"
    # its code edit stays blocked: nothing to implement from
    assert any(o["binding"] == "body" for o in acc["open"])


def test_escalation_clears_when_footprint_changes(host_ws: Workspace):
    b = next(x for x in host_ws.next_bindings(agent="Architect", limit=10)["bindings"] if x["target_key"].endswith("#S2"))
    for _ in range(3):
        host_ws.submit_binding(b["target_key"], "signature", "nope")
    host_ws.edit("Req", [{"op": "set", "key": "Criterion#S2.1", "values": {"text": "completing a done task returns HTTP 409"}}], "Analyst")
    fresh = next(x for x in host_ws.next_bindings(agent="Architect", limit=10)["bindings"] if x["target_key"] == b["target_key"])
    assert fresh["attempts"] == 0 and "HTTP 409" in fresh["prompt"]
    assert host_ws.submit_binding(b["target_key"], "signature", GOOD_SIGNATURE)["status"] == "accepted"


def test_submit_edge_cases(host_ws: Workspace):
    fill_all(host_ws)
    op = "Story2Operation::op::s=UserStory#S1"
    assert host_ws.submit_binding(op, "signature", GOOD_SIGNATURE)["status"] == "already_accepted"
    host_ws.edit("Req", [{"op": "delete", "key": "UserStory#S1"}], "Analyst")
    assert host_ws.submit_binding(op, "signature", GOOD_SIGNATURE)["status"] == "stale"
    import pytest

    from agenthot.workspace import WorkspaceError

    with pytest.raises(WorkspaceError, match="no stochastic binding"):
        host_ws.submit_binding(op, "name", "x")  # structural, not @llm
    with pytest.raises(WorkspaceError, match="malformed"):
        host_ws.submit_binding("garbage", "signature", "x")


def test_next_bindings_refused_for_engine_backends(project):
    import pytest

    from agenthot.workspace import WorkspaceError

    ws = Workspace(project, backend="mock")
    ws.init("devteam")
    with pytest.raises(WorkspaceError, match="sampled by the engine"):
        ws.next_bindings()


def test_value_for_an_outdated_footprint_is_refused(host_ws: Workspace):
    """A value is accepted only for the footprint its prompt showed."""
    for b in host_ws.next_bindings(agent="Architect", limit=10)["bindings"]:
        host_ws.submit_binding(b["target_key"], b["binding"], GOOD_SIGNATURE, b["footprint_version"])
    body = next(b for b in host_ws.next_bindings(agent="Developer", limit=10)["bindings"] if b["target_key"].endswith("op_s2"))
    # meanwhile the requirement changes -> the body's footprint (criteria) moves on
    host_ws.edit("Req", [{"op": "set", "key": "Criterion#S2.1", "values": {"text": "returns HTTP 409"}}], "Analyst")
    r = host_ws.submit_binding(body["target_key"], "body", GOOD_BODY, body["footprint_version"])
    assert r["status"] == "stale" and "fetch it again" in r["reason"]
    fresh = next(b for b in host_ws.next_bindings(agent="Developer", limit=10)["bindings"] if b["target_key"] == body["target_key"])
    assert fresh["footprint_version"] != body["footprint_version"] and "HTTP 409" in fresh["prompt"]
    assert host_ws.submit_binding(fresh["target_key"], "body", GOOD_BODY, fresh["footprint_version"])["status"] == "accepted"


def test_edit_propagates_structure_immediately(host_ws: Workspace):
    fill_all(host_ws)
    r = host_ws.edit("Req", [{"op": "set", "key": "Criterion#S2.1", "values": {"text": "returns HTTP 409"}}], "Analyst")
    assert r["open_obligations"] == 3
    # the structural copy of the criteria on the operation is already updated
    assert "HTTP 409" in host_ws.show("Arch", "Operation#op_s2")["element"]["criteria"]
    assert host_ws.status()["bindings"] == {"fresh": 4, "stale": 3}
