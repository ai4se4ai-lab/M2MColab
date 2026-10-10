"""AutoM2M: checker (W1-W6), compiler + runtime (phi), attribution (Alg. 4) and repair."""
from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from agenthot.compiler import compile_team
from agenthot.llm.base import LLMBackend
from agenthot.session import Session
from autom2m.attribution import Attributor, attribute_all, derive
from autom2m.checker import check
from autom2m.lift import normalize, split_doctests
from autom2m.repair import apply_delta, classify_delta, widen_delta
from autom2m.typed_team import load_team, parse_team
from autom2m.vlib import RunContext
from evaluation.benchmarks.tasks import MethodSpec, Task
from evaluation.harness.workbench import TaskWorkbench
from evaluation.rq2.mutate import OPERATORS, mutants

TEAMS = Path(__file__).resolve().parents[1] / "teams"

LISTING_6 = """W1  Design2Impl.body: footprint path 'd.returnType':
        Design!MethodDesign has no feature 'returnType'
W2  view Test written by ['Developer', 'Tester']; needs exactly one writer
W4  goal (Example, all, checked): anchor features {call, expected}
        reach no behaviour-checked value
W5  done-clause Tester.says('ALL TESTS PASS') is not an engine clause
W6  Impl2Run.verdict needs ['exec']; writer Tester has []
REJECTED: 5 violation(s)"""


def _team(name: str) -> dict:
    return normalize(json.loads((TEAMS / f"{name}.json").read_text()))


def test_seeded_proposal_yields_listing_6():
    res = check(_team("devteam_proposal"))
    assert [d.cond for d in res.diagnostics] == ["W1", "W2", "W4", "W5", "W6"]
    assert res.report() == LISTING_6


def test_requirements_pilot_rejected_for_unverified_work():
    res = check(load_team(TEAMS / "reqteam_pilot.json"))
    assert res.conds() == {"W4"}
    assert any("reaches no behaviour-checked value" in d.message for d in res.diagnostics)


@pytest.mark.parametrize("name", ["devteam_admitted", "devteam_admitted_g2", "reqteam_admitted",
                                  "reqteam_admitted_g2", "classeval_reference"])
@pytest.mark.parametrize("w4", ["two-sided", "one-sided", "path-only"])
def test_admitted_teams(name, w4):
    team = json.loads((TEAMS / f"{name}.json").read_text())
    assert check(normalize(team) if team["goal_view"] == "Goal" else team, w4=w4).admitted


def test_every_operator_hits_its_condition_on_devteam_g2():
    for op, _site, m in mutants(_team("devteam_admitted_g2")):
        res = check(m)
        assert not res.admitted, op
        assert OPERATORS[op][1] in res.conds(), (op, res.report())


def test_detach_caught_only_by_two_sided_anchoring():
    detached = next(m for op, _s, m in mutants(_team("devteam_admitted")) if op == "detach_goal")
    assert "W4" in check(detached).conds()
    assert check(detached, w4="path-only").admitted
    assert check(detached, w4="one-sided").admitted  # test_valid(m) still reads the examples


def test_deliverables_must_be_declared():
    team = json.loads((TEAMS / "reqteam_admitted.json").read_text())
    no_ops = next(m for op, s, m in mutants(team) if op == "delete_rule" and s == "Story2Operation")
    assert check(no_ops).admitted  # G1: every checked obligation still reaches a check
    g2 = copy.deepcopy(no_ops)
    g2["goal"].append({"class": "UserStory", "scope": "s.status = 'accepted'", "mode": "delivered"})
    assert "W4" in check(g2).conds()


def test_stratification_and_cycle():
    team = _team("devteam_admitted")
    t = copy.deepcopy(team)
    t["handoffs"][2]["rules"][0]["bind"]["name"] = "d.contract"  # structural binding reads an LLM value
    assert any("stratification" in d.message for d in check(t).diagnostics)
    t = copy.deepcopy(team)
    t["handoffs"].append({"name": "Back", "sources": ["Code"], "target": "Design", "rules": []})
    assert "W5" in check(t).conds()


def test_lift_moves_doctests_out_of_docstrings():
    doc, ex = split_doctests("Add a task.\n>>> b = TaskBoard(); b.add_task('x')\n1\n\nMore text.")
    assert ">>>" not in doc and "Add a task." in doc and "More text." in doc
    assert ex == [("b = TaskBoard(); b.add_task('x')", "1")]


# ---------------------------------------------------------------------------
# compile + run with a scripted LLM on a one-function task
# ---------------------------------------------------------------------------

TASK = Task(
    task_id="toy/1", bench="humanevalplus", kind="function", entry="add",
    prompt='def add(a, b):\n    """Return a + b.\n    >>> add(1, 2)\n    3\n    """\n',
    imports="", description="Return a + b.",
    methods=[MethodSpec("add", "def add(a, b):", "Return a + b.\n>>> add(1, 2)\n3", ">>> add(1, 2)\n3")],
)

GOOD_TESTS = ("```python\nimport unittest\nclass T(unittest.TestCase):\n    def test_add(self):\n"
              "        self.assertEqual(add(1, 2), 3)\n        self.assertEqual(add(2, 2), 4)\n```")
WRONG_TESTS = ("```python\nimport unittest\nclass T(unittest.TestCase):\n    def test_add(self):\n"
               "        self.assertEqual(add(1, 2), 3)\n        self.assertEqual(add(2, 2), 5)\n```")
GOOD_CODE = "```python\ndef add(a, b):\n    return a + b\n```"
BAD_CODE = "```python\ndef add(a, b):\n    return a - b\n```"


class Scripted(LLMBackend):
    """Tests from a queue, contracts fixed, code from a queue (last one repeats)."""

    name = "scripted"

    def __init__(self, code_answers, test_answers=(GOOD_TESTS,), code_rule=None):
        self.code_answers = list(code_answers)
        self.test_answers = list(test_answers)
        self.code_rule = code_rule
        self.calls = 0

    def generate(self, prompt, *, temperature=0.2, **kw):
        self.calls += 1
        if "Write unit tests" in prompt:
            return self.test_answers.pop(0) if len(self.test_answers) > 1 else self.test_answers[0]
        if "State a contract" in prompt:
            return "add returns the sum of a and b"
        if self.code_rule is not None:
            return self.code_rule(prompt)
        return self.code_answers.pop(0) if len(self.code_answers) > 1 else self.code_answers[0]


def _session(llm, team_json=None, k=2, lenient=False):
    team = parse_team(team_json or _team("classeval_reference"))
    ct = compile_team(team, TASK, lenient=lenient)
    wb = TaskWorkbench(TASK)
    s = Session(ct, llm, RunContext(wb), k=k)
    wb.bind(s.current_bodies)
    return s


def test_run_reaches_phi_and_covers_examples_through_footprints():
    s = _session(Scripted([GOOD_CODE]))
    res = s.run()
    assert res.phi, [f.reason for f in res.failures]
    assert res.accepted == {"add": True}
    assert res.clauses == {"noEsc": True, "fresh": True, "valid": True, "cover(G)": True}
    conn = s.connections()
    assert any(k.startswith("Example#E1.1") for k in conn)  # the Example is connected by the test's footprint


def test_stamps_cover_validator_reads():
    s = _session(Scripted([GOOD_CODE]))
    s.run()
    link = next(iter(s.ct.team.traces["Method2Test"].links()))
    before = dict(link.stamps)
    ex = s.ct.team.roots["Goal"].all_Example[0]
    ex.expected = "4"  # a validator read (and footprint value) changes
    assert not s.rt.stamps_fresh()
    assert before


def test_library_clause_examples_pass():
    t = _team("classeval_reference")
    t["done"] = t["done"] + ["examples_pass"]
    assert check(t).admitted
    res = _session(Scripted([GOOD_CODE]), t).run()
    assert res.phi and res.clauses["examples_pass"]


def test_wrong_code_escalates_and_is_a_sampling_fault():
    s = _session(Scripted([BAD_CODE, BAD_CODE, GOOD_CODE]))
    res = s.run()
    assert not res.phi
    assert any(f.clause == "noEsc" and f.rule == "Design2Impl" for f in res.failures)
    reports, calls = attribute_all(s, res.failures)
    rep = reports[0]
    assert (rep.rule, rep.binding, rep.agent) == ("Design2Impl", "body", "Developer")
    assert rep.fault_class == "sampling" and calls == 1


def test_narrow_footprint_is_a_footprint_fault_with_a_minimal_widening():
    t = _team("classeval_reference")
    t["handoffs"][2]["rules"][0]["llm"][0]["footprint"] = ["d.method.signature"]
    rule = lambda p: GOOD_CODE if "Return a + b" in p else BAD_CODE  # needs the docstring  # noqa: E731
    s = _session(Scripted([], code_rule=rule), t)
    res = s.run()
    reports, _ = attribute_all(s, res.failures)
    rep = reports[0]
    assert rep.fault_class == "footprint" and rep.hops == 1
    assert len(rep.widened) == 1 and rep.widened[0].endswith(".method.docstring")  # d. or t. (joined)
    fixed = parse_team(widen_delta(t, {(rep.rule, rep.binding): rep.widened}))
    assert check(fixed).admitted
    ar = apply_delta(s, fixed, TASK)
    assert ar.mode == "in-place"
    assert ar.session.run().phi


def test_wrong_upstream_test_is_an_upstream_fault():
    t = _team("classeval_reference")
    t["handoffs"][1]["rules"][0]["llm"][0]["validator"] = [{"id": "weak"}]  # a weak validator accepts wrong tests
    s = _session(Scripted([GOOD_CODE], test_answers=[WRONG_TESTS, GOOD_TESTS]), t)
    res = s.run()
    assert not res.phi
    att = Attributor(s)
    rep = att.attribute(next(f for f in res.failures if f.clause == "noEsc"))
    assert rep.fault_class == "upstream"
    assert rep.upstream["rule"] == "Method2Test" and rep.responsible()["agent"] == "Tester"


def test_unsatisfiable_validator_is_a_specification_fault_and_repair_keeps_values():
    team_json = _team("classeval_reference")
    bad = copy.deepcopy(team_json)
    bad["handoffs"][2]["rules"][0]["llm"][0]["validator"] = [{"id": "unsat"}]
    s = _session(Scripted([GOOD_CODE]), bad, k=1)
    res = s.run()
    reports, _ = attribute_all(s, res.failures)
    assert reports[0].fault_class == "specification"
    tests_stamped = sum(len(l.stamps) for l in s.ct.team.traces["Method2Test"].links())
    assert classify_delta(parse_team(bad), parse_team(team_json)) == "in-place"
    ar = apply_delta(s, parse_team(team_json), TASK)
    assert ar.mode == "in-place" and ar.kept >= tests_stamped
    assert ar.session.run().phi


def test_flaky_validator_is_a_validator_fault():
    t = _team("classeval_reference")
    t["handoffs"][2]["rules"][0]["llm"][0]["validator"] += [{"id": "flaky"}]
    s = _session(Scripted([GOOD_CODE]), t, k=1)
    s.ctx.rng.seed(3)
    res = s.run()
    if res.phi:
        pytest.skip("the flaky validator happened to accept")
    rep = attribute_all(s, res.failures)[0][0]
    assert rep.fault_class in ("validator", "sampling")


def test_exhaustive_record_derives_both_budgets():
    steps = [{"step": "sampling", "draws": [False, True, False], "pre_calls": 0},
             {"step": "footprint1", "draws": [True, True, True], "pre_calls": 0}]
    assert derive(steps, 1) == ("footprint", 2)
    assert derive(steps, 3) == ("sampling", 2)


def test_typed_nc_compiles_ill_typed_bindings_to_escalations():
    t = _team("devteam_proposal")
    s = _session(Scripted([GOOD_CODE]), t, lenient=True)
    llm = s.llm
    res = s.run()
    esc = [e for e in res.escalations if e.rule == "Design2Impl"]
    assert esc and "footprint cannot be evaluated" in esc[0].reason
    assert not res.phi and llm.calls >= 1
