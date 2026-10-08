"""AutoM2M: checker conditions, compile + run, attribution and repair."""
from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from agentm2m.auto.attribution import attribute_all
from agentm2m.auto.checker import check
from agentm2m.auto.compile import Session, compile_team
from agentm2m.auto.repair import apply_delta, normalize
from agentm2m.auto.typed_team import load_team, parse_team
from agentm2m.auto.vlib import RunContext
from agentm2m.llm.base import LLMBackend
from evaluation.benchmarks.tasks import MethodSpec, Task
from evaluation.harness.workbench import TaskWorkbench
from evaluation.rq2.mutate import OPERATORS, mutants

TEAMS = Path(__file__).resolve().parents[1] / "teams"


def test_seeded_proposal_yields_exactly_the_seeded_defects():
    res = check(load_team(TEAMS / "devteam_proposal.json"))
    assert [d.cond for d in res.diagnostics] == ["W1", "W2", "W4", "W5", "W6"]


def test_pilot_team_rejected_for_unverified_views():
    res = check(load_team(TEAMS / "chakin_pilot.json"))
    assert [d.cond for d in res.diagnostics] == ["W4", "W4"]
    assert all("reaches no behavioural check" in d.message for d in res.diagnostics)


@pytest.mark.parametrize("name", ["devteam_admitted", "devteam_admitted_g2", "chakin_repaired",
                                  "chakin_repaired_g2", "classeval_reference"])
def test_admitted_teams(name):
    assert check(load_team(TEAMS / f"{name}.json")).admitted


def test_every_operator_hits_its_condition_on_devteam_g2():
    team = json.loads((TEAMS / "devteam_admitted_g2.json").read_text())
    for op, _site, m in mutants(team):
        res = check(m)
        assert not res.admitted, op
        assert OPERATORS[op][1] in res.conds(), (op, res.report())


def test_naive_w4_misses_detached_team():
    team = json.loads((TEAMS / "devteam_admitted.json").read_text())
    detached = next(m for op, _s, m in mutants(team) if op == "detach_goal")
    assert not check(detached).admitted
    assert check(detached, anchored=False).admitted


def test_cycle_detected():
    team = json.loads((TEAMS / "devteam_admitted.json").read_text())
    team["handoffs"].append({"name": "Back", "sources": ["Code"], "target": "Arch", "rules": []})
    assert "W5" in check(team).conds()


# ---------------------------------------------------------------------------
# compile + run with a scripted LLM on a one-function task
# ---------------------------------------------------------------------------

TASK = Task(
    task_id="toy/1", bench="humanevalplus", kind="function", entry="add",
    prompt='def add(a, b):\n    """Return a + b.\n    >>> add(1, 2)\n    3\n    """\n',
    imports="", description="Return a + b.",
    methods=[MethodSpec("add", "def add(a, b):", "Return a + b.\n>>> add(1, 2)\n3", ">>> add(1, 2)\n3")],
)

GOOD_TESTS = "```python\nimport unittest\nclass T(unittest.TestCase):\n    def test_add(self):\n        self.assertEqual(add(2, 2), 4)\n```"
GOOD_CODE = "```python\ndef add(a, b):\n    return a + b\n```"
BAD_CODE = "```python\ndef add(a, b):\n    return a - b\n```"


class Scripted(LLMBackend):
    name = "scripted"

    def __init__(self, code_answers):
        self.code_answers = list(code_answers)
        self.calls = 0

    def generate(self, prompt, *, temperature=0.2, **kw):
        self.calls += 1
        if "Write unit tests" in prompt:
            return GOOD_TESTS
        return self.code_answers.pop(0) if len(self.code_answers) > 1 else self.code_answers[0]


def _session(llm):
    team = parse_team(normalize(json.loads((TEAMS / "classeval_reference.json").read_text())))
    ct = compile_team(team, TASK)
    wb = TaskWorkbench(TASK)
    s = Session(ct, llm, RunContext(wb), k=2)
    wb.bind(s.current_bodies)
    return s


def test_run_reaches_phi_with_correct_values():
    s = _session(Scripted([GOOD_CODE]))
    res = s.run()
    assert res.phi, [f.reason for f in res.failures]
    assert res.accepted == {"add": True}


def test_wrong_code_escalates_and_is_attributed_as_sampling_fault():
    s = _session(Scripted([BAD_CODE, BAD_CODE, GOOD_CODE]))
    res = s.run()
    assert not res.phi
    assert any(f.clause == "noObl" and f.rule == "Method2Impl" for f in res.failures)
    reports, calls = attribute_all(s, res.failures, r=1)
    rep = reports[0]
    assert (rep.rule, rep.binding, rep.agent) == ("Method2Impl", "code", "Developer")
    assert rep.fault_class == "sampling" and calls >= 1


def test_unsatisfiable_validator_is_a_specification_fault_and_repair_keeps_values():
    team_json = normalize(json.loads((TEAMS / "classeval_reference.json").read_text()))
    bad = copy.deepcopy(team_json)
    bad["handoffs"][1]["rules"][0]["llm"][0]["validator"] = [{"id": "unsat"}]
    s = _session(Scripted([GOOD_CODE]))
    s2 = Session(compile_team(parse_team(bad), TASK), s.llm, s.ctx, k=1)
    s.ctx.bench.bind(s2.current_bodies)
    res = s2.run()
    reports, _ = attribute_all(s2, res.failures, r=1)
    assert reports[0].fault_class == "specification"
    tests_stamped = sum(len(l.stamps) for l in s2.ct.team.traces["Goal2Test"].links())
    ar = apply_delta(s2, parse_team(team_json), TASK)
    assert ar.mode == "in-place" and ar.kept >= tests_stamped
    res2 = ar.session.run()
    assert res2.phi
