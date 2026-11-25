"""AutoM2M-Lean: wiring inference, sketches, and the cost mechanisms of
attribution and sampling."""
from __future__ import annotations

import copy
import json

import pytest

from agenthot.engine.binding import SAMPLER_FIXPOINT
from autom2m.attribution import attribute_all, derive
from autom2m.checker import check
from autom2m.examples import EXAMPLES
from autom2m.lift import normalize
from autom2m.sketch import SKETCH_SCHEMA, expand, is_sketch, to_sketch
from autom2m.wiring import infer
from tests.test_auto_checker_runtime import BAD_CODE, GOOD_CODE, Scripted, _session, _team


@pytest.mark.parametrize("i", range(len(EXAMPLES)))
def test_sketch_of_each_worked_example_is_wired_back_to_an_admitted_team(i):
    team = EXAMPLES[i][1]
    sk = to_sketch(team)
    assert is_sketch(sk)
    wired = infer(expand(sk))
    assert check(normalize(wired)).admitted
    assert wired["deliverable"] == team["deliverable"]
    # the inferred join guards are the hand-written ones (up to orientation)
    for h0 in team["handoffs"]:
        for r0 in h0["rules"]:
            r1 = next(r for h in wired["handoffs"] for r in h["rules"] if r["name"] == r0["name"])
            if r0["guard"]:
                assert sorted(r0["guard"].split(" = ")) == sorted(r1["guard"].split(" = "))
            assert {k: v for k, v in r1["bind"].items() if k in r0["bind"]} == r0["bind"]


def test_inference_is_idempotent_and_keeps_admitted_teams_admitted():
    for name in ("classeval_reference",):
        t = _team(name)
        w = infer(t)
        assert check(normalize(w)).admitted
        assert json.dumps(infer(w), sort_keys=True) == json.dumps(w, sort_keys=True)


def test_inference_repairs_typical_wiring_slips():
    t = copy.deepcopy(EXAMPLES[1][1])
    rules = [r for h in t["handoffs"] for r in h["rules"]]
    rules[0]["llm"][0]["validator"] = [{"id": "nonempty", "args": {"plan": "d.plan"}}]  # extra argument
    rules[2]["llm"][0]["footprint"].append("d.returnType")  # ill-typed path
    rules[2]["guard"] = None  # missing join
    rules[2]["bind"] = {}  # unbound required features
    t["writes"].pop("Designer")  # a view without writer
    t["done"] = ["Tester.says('ok')"]  # an agent claim
    res = check(normalize(t))
    assert {"W1", "W2", "W3", "W5"} <= res.conds()
    assert check(normalize(infer(t))).admitted


def test_goal_aliases_and_invented_goal_classes_are_retyped_by_usage():
    t = copy.deepcopy(EXAMPLES[0][1])
    r = t["handoffs"][0]["rules"][0]
    r["from"] = [{"var": "m", "type": "Goal!Function"}]  # not a goal class; used like a Method
    assert not check(normalize(t)).admitted
    assert check(normalize(infer(t))).admitted


def test_cycles_are_broken_and_dead_views_removed():
    t = copy.deepcopy(EXAMPLES[1][1])
    t["views"]["Notes"] = {"classes": {"Note": {"attributes": {"name": "string", "text": "string"}, "references": {}}}}
    t["agents"].append({"name": "Scribe", "role": "notes", "tools": []})
    t["writes"]["Scribe"] = ["Notes"]
    t["handoffs"].append({"name": "Code2Notes", "sources": ["Code"], "target": "Notes", "rules": [{
        "name": "Impl2Note", "from": [{"var": "i", "type": "Code!MethodImpl"}], "guard": None,
        "to": {"var": "n", "type": "Notes!Note"}, "bind": {}, "llm": [{"feature": "text", "prompt": "note",
                                                                        "footprint": ["i.code"],
                                                                        "validator": [{"id": "nonempty"}]}]}]})
    assert "W4" in check(normalize(t)).conds()  # Notes is never verified
    w = infer(t)
    assert "Notes" not in w["views"] and check(normalize(w)).admitted


def test_sketch_schema_requires_every_property():
    def walk(s):
        if s.get("type") == "object" and "properties" in s:
            assert set(s["required"]) == set(s["properties"])
            for v in s["properties"].values():
                walk(v)
        if s.get("type") == "array":
            walk(s["items"])
    walk(SKETCH_SCHEMA)


# ---------------------------------------------------------------- attribution

def test_pooled_group_test_skips_the_sequential_steps_for_a_specification_fault():
    bad = _team("classeval_reference")
    bad["handoffs"][2]["rules"][0]["llm"][0]["validator"] = [{"id": "unsat"}]
    seq = _session(Scripted([GOOD_CODE]), bad, k=1)
    r1 = seq.run()
    reps_seq, calls_seq = attribute_all(seq, r1.failures)
    lean = _session(Scripted([GOOD_CODE]), bad, k=1)
    r2 = lean.run()
    reps_lean, calls_lean = attribute_all(lean, r2.failures, pooled=True, equivalence=True, cache=True)
    assert reps_seq[0].fault_class == reps_lean[0].fault_class == "specification"
    assert calls_lean < calls_seq
    assert reps_lean[0].steps[0]["step"] == "pooled"


def test_pooled_attribution_keeps_a_sampling_fault():
    s = _session(Scripted([BAD_CODE, BAD_CODE, GOOD_CODE]))
    res = s.run()
    reports, _ = attribute_all(s, res.failures, pooled=True, equivalence=True)
    assert reports[0].fault_class == "sampling"


def test_verdict_cache_reuses_an_identical_experiment():
    bad = _team("classeval_reference")
    bad["handoffs"][2]["rules"][0]["llm"][0]["validator"] = [{"id": "unsat"}]
    s = _session(Scripted([GOOD_CODE]), bad, k=1)
    res = s.run()
    _r1, c1 = attribute_all(s, res.failures, pooled=True, equivalence=True, cache=True)
    r2, c2 = attribute_all(s, res.failures, pooled=True, equivalence=True, cache=True)
    assert c1 > 0 and c2 == 0 and r2[0].steps[0]["step"] == "cached"


def test_derive_pooled_and_equivalent():
    steps = [{"step": "pooled", "draws": [False, False, False], "pre_calls": 2},
             {"step": "sampling", "draws": [False, False, False], "pre_calls": 0, "prompt": "a"},
             {"step": "footprint1", "draws": [False, False, False], "pre_calls": 0, "prompt": "a"},
             {"step": "upstream:R.f", "draws": [False, False, False], "pre_calls": 1, "prompt": "b"}]
    assert derive(steps, 3) == ("specification", 3 + 3 + 1 + 3)
    assert derive(steps, 3, equivalent=True) == ("specification", 3 + 1 + 3)
    assert derive(steps, 3, pooled=True, equivalent=True) == ("specification", 2 + 3)
    steps[2]["draws"] = [False, True, False]
    steps[2]["prompt"] = "c"
    steps[0]["draws"] = [False, True, True]
    assert derive(steps, 3, pooled=True, equivalent=True)[0] == "footprint"


# ---------------------------------------------------------------- sampling

def test_fixpoint_stops_repeated_rejections():
    t = _team("classeval_reference")
    off = _session(Scripted([BAD_CODE]), t, k=3)
    off.run()
    on = _session(Scripted([BAD_CODE]), t, k=3)
    on.stop_on_fixpoint = True
    on.run()
    assert SAMPLER_FIXPOINT.get() is False  # reset after the run
    assert on.llm.calls == off.llm.calls - 1  # the third, identical retry prompt is never sent
