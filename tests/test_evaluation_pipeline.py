"""The evaluation harness without an LLM server: the AutoM2M loop with a
scripted LLM (AutoM2M, Typed-Ref, Typed-NC), the engine's handling of typed
LLM values, fault-injection helpers, transcript scoring, failure coding,
statistics, and the analysis pipeline on synthetic logs."""
from __future__ import annotations

import copy
import importlib
import json
import random
from pathlib import Path

import pytest

from agenthot.llm.base import LLMBackend
from autom2m.checker import check
from autom2m.lift import normalize
from autom2m.loop import AutoM2M
from evaluation.analysis import stats
from evaluation.benchmarks.tasks import MethodSpec, Task
from evaluation.coding import COMPOSITION, _norm, render_run
from evaluation.harness.workbench import TaskWorkbench

TEAMS = Path(__file__).resolve().parents[1] / "teams"
TASK = Task(
    task_id="toy/1", bench="humanevalplus", kind="function", entry="add",
    prompt='def add(a, b):\n    """Return a + b.\n    >>> add(1, 2)\n    3\n    """\n',
    imports="", description="Return a + b.",
    methods=[MethodSpec("add", "def add(a, b):", "Return a + b.\n>>> add(1, 2)\n3", ">>> add(1, 2)\n3")],
)
TESTS = ("```python\nimport unittest\nclass T(unittest.TestCase):\n    def test_add(self):\n"
         "        self.assertEqual(add(1, 2), 3)\n        self.assertEqual(add(2, 2), 4)\n```")
CODE = "```python\ndef add(a, b):\n    return a + b\n```"


class Scripted(LLMBackend):
    """Builder answers with a fixed team; bindings get contract / tests / code."""

    name = "scripted"

    def __init__(self, team: dict) -> None:
        self.team = team
        self.role = "binding"

    def generate(self, prompt, *, temperature=0.2, format=None, **kw):
        if format is not None:
            return json.dumps(self.team)
        if "Write unit tests" in prompt:
            return TESTS
        if "State a contract" in prompt:
            return "returns the sum"
        return CODE


def _team(name: str) -> dict:
    return json.loads((TEAMS / f"{name}.json").read_text())


def test_autom2m_loop_admits_runs_and_records_instrumentation(tmp_path):
    llm = Scripted(_team("classeval_reference"))
    out = AutoM2M(llm, workdir=tmp_path).solve(TASK, TASK.prompt, TaskWorkbench(TASK))
    assert out.status == "done" and out.phi and out.admitted_round == 0
    assert out.checks == 1 and out.check_seconds > 0
    assert {"propose", "compile", "run"} <= set(out.timing)
    assert any(e["status"] == "accepted" and e["agent"] == "Developer" for e in out.events)


def test_autom2m_revises_from_diagnostics(tmp_path):
    proposal, admitted = _team("devteam_proposal"), _team("classeval_reference")

    class Reviser(Scripted):
        def __init__(self):
            super().__init__(proposal)
            self.n = 0

        def generate(self, prompt, *, format=None, **kw):
            if format is not None:
                self.n += 1
                return json.dumps(proposal if self.n == 1 else admitted)
            return super().generate(prompt, **kw)

    out = AutoM2M(Reviser(), builder="v1", workdir=tmp_path).solve(TASK, TASK.prompt, TaskWorkbench(TASK))
    assert out.first_violation == "W1" and out.admitted_round == 1 and out.checks == 2
    assert [d[:2] for d in out.diagnostics[0]] == ["W1", "W2", "W4", "W5", "W6"]
    assert out.status == "done"


def test_typed_nc_runs_a_defective_team_without_crashing(tmp_path):
    out = AutoM2M(Scripted(_team("devteam_proposal")), check_enabled=False, repair_enabled=False,
                  workdir=tmp_path).solve(TASK, TASK.prompt, TaskWorkbench(TASK))
    assert out.status in ("done", "failed") and out.checks == 0
    assert {d[:2] for d in out.unchecked_diagnostics} == {"W1", "W2", "W4", "W5", "W6"}


def test_engine_coerces_typed_llm_values_and_w1_rejects_many_valued_targets():
    from agenthot.engine.binding import _coerce
    from agenthot.metamodel.builder import MetamodelBuilder

    mm = MetamodelBuilder("V", "http://v")
    c = mm.eclass("C")
    mm.attribute(c, "ok", "boolean")
    mm.attribute(c, "n", "int")
    mm.attribute(c, "tags", "string", many=True)
    obj = c()
    assert _coerce(obj, "ok", "True, all tests pass") == (True, "")
    assert _coerce(obj, "n", "42 items") == (42, "")
    assert _coerce(obj, "ok", "maybe")[1] and _coerce(obj, "tags", "x")[1]
    t = normalize(_team("classeval_reference"))
    t["views"]["Code"]["classes"]["MethodImpl"]["attributes"]["body"] = "string*"
    assert any("single-valued" in d.message for d in check(t).diagnostics)


def test_injection_helpers():
    from evaluation.rq4.inject import Degrade, _wrong, injected_team

    assert _wrong("3") == "4" and _wrong("'a'") == "'a_x'" and _wrong("True") == "False" and _wrong("foo(") is None
    base = normalize(_team("classeval_reference"))
    impl = lambda t: t["handoffs"][2]["rules"][0]["llm"][0]  # noqa: E731
    assert impl(injected_team(base, "footprint"))["footprint"] == ["d.method.signature"]
    assert impl(injected_team(base, "specification"))["validator"][-1] == {"id": "unsat"}
    assert impl(injected_team(base, "validator"))["validator"][-1] == {"id": "flaky"}

    class Inner(LLMBackend):
        name, role, keep_text = "inner", "binding", False

        def generate(self, prompt, **kw):
            return "real"

    d = Degrade(Inner(), "add", "You are the Developer", p=1.0)
    assert "return None" in d.generate("You are the Developer ... def add(a, b):")
    assert d.generate("You are the Tester ... def add(a, b):") == "real"


def test_transcript_steps_and_scores():
    from evaluation.rq4.transcript import score, score_trace, steps_of

    row = {"truth": {"class": "upstream", "binding": "Method2Test.code", "agent": "Tester", "method": "add"},
           "registry": {"Method2Test.code": "You are the Tester. Write tests", "Design2Impl.body": "You are the Developer. Implement"},
           "owners": {"Method2Test.code": "Tester", "Design2Impl.body": "Developer"},
           "clean_summary": [{"head": "You are the Tester. Write tests", "method": "add", "output": "tests", "checks": ["accepted"]}],
           "transcript": [{"role": "binding", "prompt": "You are the Developer. Implement def add(a, b):", "output": "x"},
                          {"role": "check", "ok": False, "reason": "failing tests"}],
           "report": {"rule": "Design2Impl", "binding": "body", "target_key": "T", "fault_class": "upstream"},
           "symptom": {"rule": "Design2Impl", "binding": "body", "target": "T"},
           "responsible": {"rule": "Method2Test", "binding": "code", "agent": "Tester", "target_key": None},
           "class_n1": "specification", "class_n3": "upstream", "calls_n1": 3, "calls_n3": 6}
    steps = steps_of(row)
    assert [s["agent"] for s in steps] == ["Tester", "Developer"] and steps[1]["checks"] == ["rejected: failing tests"]
    sc = score({"agent": "Tester", "step": 0, "class": "Upstream fault"}, row, steps)
    assert sc["agent_ok"] and sc["step_ok"] and sc["class_ok"]
    tr = score_trace(row)
    assert tr["symptom_ok"] and tr["binding_ok"] and tr["class_adaptive"] and not tr["class_n1"]


def test_coding_rendering_and_codes():
    assert _norm("D2 hand-off mismatch") == "D2" and _norm("agent reasoning") == "reasoning" and _norm("??") is None
    assert COMPOSITION == {"D1", "D2", "D3", "D4", "D5"}
    typed = {"detail": {"final_team": normalize(_team("classeval_reference")), "phi": False,
                        "events": [{"agent": "Developer", "binding": "Design2Impl.body", "target": "x::m=Method#add",
                                    "status": "escalated", "value": "def add", "reason": "failing tests"}]},
             "score": {"tests_passed": 0, "tests_run": 3}}
    text = render_run(typed)
    assert "Developer" in text and "escalated" in text and "autom2m" not in text.lower()
    free = {"detail": {"transcript": [{"agent": "Coder", "content": "TERMINATE", "kind": "message"}],
                       "team": {"agents": []}}, "score": {}}
    assert "TERMINATE" in render_run(free)


def test_statistics():
    q, p = stats.cochran_q([[1, 1, 1, 1, 0, 1], [0, 0, 1, 0, 0, 0], [1, 1, 1, 1, 1, 1]])
    assert q > 0 and 0 <= p <= 1
    point, lo, hi = stats.cluster_bootstrap({i: [i % 2] for i in range(40)}, lambda xs: sum(xs) / len(xs), b=200)
    assert lo <= point <= hi
    assert stats.cliff_magnitude(0.5) == "L" and stats.cliff_magnitude(0.0) == "N"


def _synthetic_results(root: Path) -> None:
    rng = random.Random(1)
    conds = ["single", "single_gate", "free", "critic", "schema", "typed_nc", "autom2m", "typed_ref"]
    rows = []
    for bench in ("classeval", "humanevalplus"):
        for t in range(12):
            for c in conds:
                for seed in (1, 2):
                    ok = rng.random() < (0.5 if c in ("single_gate", "autom2m") else 0.3)
                    r = {"bench": bench, "task": f"{bench}/{t}", "condition": c, "model": "m:7b", "seed": seed,
                         "status": "done" if ok else "failed", "success": ok, "test_pass_rate": float(ok),
                         "declared_done": ok or rng.random() < 0.3, "seconds": rng.uniform(10, 100),
                         "tokens": {"builder": {"out_tokens": 100, "seconds": 1}}, "total": {"out_tokens": rng.randint(500, 9000)}}
                    if c in ("typed_nc", "autom2m", "typed_ref"):
                        r.update(admitted=True, admitted_round=rng.choice([0, 0, 1]), checks=2, check_seconds=0.001,
                                 phi=ok, escalations=0 if ok else 1, timing={"propose": 5, "run": 30, "validators": 2},
                                 team_shape="3|A,B,C|X,Y|test_valid", repairs=[], fault_classes=[])
                    rows.append(r)
    (root / "raw").mkdir(parents=True)
    (root / "raw" / "x.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    (root / "rq1").mkdir()
    (root / "rq1" / "codes_whowhen.jsonl").write_text("".join(json.dumps(
        {"id": f"ww_auto/{i}", "source": "ww_auto", "coder_a": {"code": c}, "coder_b": {"code": c}, "final": c,
         "secondary": None, "agree": True}) + "\n" for i, c in enumerate(["D1", "D2", "reasoning", "D4", "tool"] * 4)))
    (root / "rq3").mkdir()
    (root / "rq3" / "codes_runs.jsonl").write_text("".join(json.dumps(
        {"id": f"{r['bench']}/m_7b/{r['condition']}/{r['task'].replace('/', '_')}__s{r['seed']}", "bench": r["bench"],
         "model": "m:7b", "condition": r["condition"], "coder_a": {"code": "D2"}, "coder_b": {"code": "D2"},
         "final": rng.choice(["D2", "reasoning"]), "secondary": None, "agree": True}) + "\n"
        for r in rows if not r["success"]))


def test_analysis_pipeline_on_synthetic_logs(tmp_path, monkeypatch):
    res, docs = tmp_path / "results", tmp_path / "docs"
    _synthetic_results(res)
    monkeypatch.setenv("AM2M_RESULTS", str(res))
    monkeypatch.setenv("AM2M_DOCS_OUT", str(docs))
    import evaluation.analysis.analyze as A
    import evaluation.analysis.export as E

    importlib.reload(E)
    importlib.reload(A)
    try:
        assert A.main([]) == 0
        summary = json.loads((res / "summary.json").read_text())
        assert summary["rq3"]["success"]["classeval"]["mean"]["autom2m"] is not None
        assert "h3b" in summary["rq3"]["success"] and summary["rq3"]["compfail"]["coded"] > 0
        assert summary["rq1"]["ww_auto"]["n"] == 20
        for name in ("tab_success_classeval.tex", "tab_success_humaneval.tex", "tab_compfail.tex", "tab_donecost.tex"):
            assert (docs / "tables" / name).exists(), name
        for name in ("fig_rq1_causes.pdf", "fig_admission.pdf"):
            assert (docs / "figures" / name).exists(), name
    finally:
        monkeypatch.delenv("AM2M_RESULTS")
        monkeypatch.delenv("AM2M_DOCS_OUT")
        importlib.reload(E)
        importlib.reload(A)


@pytest.mark.parametrize("name", ["devteam_admitted_g2", "classeval_reference", "pair_team"])
def test_fixture_teams_are_admitted_and_compile(name, tmp_path):
    from agenthot.compiler import compile_team
    from autom2m.typed_team import parse_team

    t = normalize(copy.deepcopy(_team(name)))
    assert check(t).admitted
    ct = compile_team(parse_team(t), TASK, tmp_path)
    assert ct.handoff_order


def test_typed_nc_survives_structural_defects(tmp_path):
    """Unchecked teams: a rule matching an undeclared view, a reference bound
    to the wrong class, a string bound to a boolean."""
    t = normalize(_team("classeval_reference"))
    t["handoffs"][1]["sources"] = ["Design"]  # Method2Test matches Goal!Method, Goal not declared
    t["views"]["Test"]["classes"]["TestCase"]["attributes"]["ok"] = "boolean"
    t["handoffs"][1]["rules"][0]["bind"]["ok"] = "m.name"  # string -> boolean
    t["handoffs"][2]["rules"][0]["bind"]["method"] = "t"  # TestCase -> Goal.Method reference
    assert not check(t).admitted
    out = AutoM2M(Scripted(t), check_enabled=False, repair_enabled=False, workdir=tmp_path).solve(
        TASK, TASK.prompt, TaskWorkbench(TASK))
    assert out.status in ("done", "failed")


def test_tests_importing_a_guessed_module_get_the_solution():
    from autom2m.pywork import run_tests

    code = "class Calc:\n    def add(self, a, b):\n        return a + b\n"
    tests = ("import calc_module\nimport unittest\nclass T(unittest.TestCase):\n"
             "    def test(self):\n        self.assertEqual(calc_module.Calc().add(1, 2), 3)\n")
    assert run_tests(code, tests)["ok"]


def test_test_valid_rejects_tests_that_crash_on_their_own_setup():
    from agenthot.compiler import compile_team
    from agenthot.session import Session
    from autom2m.typed_team import parse_team
    from autom2m.vlib import CURRENT, RunContext, v_test_valid

    ct = compile_team(parse_team(normalize(_team("pair_team"))), TASK)
    wb = TaskWorkbench(TASK)
    s = Session(ct, Scripted({}), RunContext(wb))
    wb.bind(s.current_bodies)
    method = ct.team.roots["Goal"].all_Method[0]
    crashing = ("```python\nimport unittest\nclass T(unittest.TestCase):\n    def test(self):\n"
                "        self.assertEqual(calc.add(1, 2), 3)\n```")  # `calc` is never created
    tok = CURRENT.set(s.ctx)
    try:
        r = v_test_valid(crashing, None, method)
        assert not r and "setup" in r.reason
        assert v_test_valid(TESTS, None, method) is True
    finally:
        CURRENT.reset(tok)


def test_v2_builder_repairs_only_the_diagnosed_fragments(tmp_path):
    """A proposal with an undeclared footprint feature is repaired by a fix
    that rewrites only the diagnosed rule; the rest is kept verbatim."""
    good = _team("pair_team")
    bad = copy.deepcopy(good)
    bad["handoffs"][1]["rules"][0]["llm"][0]["footprint"] = ["t.method.signature", "t.missing"]

    class V2(Scripted):
        def __init__(self):
            super().__init__(bad)
            self.prompts = []

        def generate(self, prompt, *, format=None, **kw):
            if isinstance(format, dict) and "fixes" in format.get("properties", {}):
                self.prompts.append(prompt)
                return json.dumps({"fixes": [{"pointer": "/handoffs/1/rules/0", "value": good["handoffs"][1]["rules"][0]}]})
            if format is not None:
                return json.dumps(bad)
            return super().generate(prompt, **kw)

    llm = V2()
    out = AutoM2M(llm, builder="v2", k_prop=2, k_rev=1, workdir=tmp_path).solve(TASK, TASK.prompt, TaskWorkbench(TASK))
    assert out.admitted_round == 1 and out.status == "done"
    assert "/handoffs/1/rules/0" in llm.prompts[0] and "t.method.docstring" in llm.prompts[0]  # path catalogue


def test_fragment_localisation():
    from autom2m.builder import fragments_for
    from autom2m.checker import check

    t = normalize(_team("devteam_proposal"))
    frags = fragments_for(t, check(t).diagnostics)
    # W4 (Example unanchored) offers the rules and views that should carry it, which covers Design2Impl's rule
    assert {"/handoffs", "/views", "/writes", "/done", "/goal", "/agents"} <= set(frags)
