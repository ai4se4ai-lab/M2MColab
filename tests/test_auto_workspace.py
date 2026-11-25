"""AutoWorkspace: task lifting, host-mode builder and binding loop, persistence,
deltas, and the unattended loop with a scripted engine LLM."""
from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from autom2m.pywork import (
    TaskSourceError,
    assemble,
    run_examples,
    task_from_source,
)
from autom2m.workspace import AutoWorkspace, AutoWorkspaceError, check_team_json
from agenthot.llm.base import LLMBackend

TEAMS = Path(__file__).resolve().parents[1] / "teams"
REF = json.loads((TEAMS / "pair_team.json").read_text())

CLASS_SRC = '''import math

class Calc:
    """A tiny calculator."""
    def __init__(self):
        self.mem = 0

    @staticmethod
    def add(a, b):
        """Add two numbers.
        >>> Calc.add(1, 2)
        3
        """
        pass

    def root(self, x):
        """Square root.
        >>> Calc().root(9)
        3.0
        """
        raise NotImplementedError
'''
FUNC_SRC = 'def add(a, b):\n    """Return a + b.\n    >>> add(1, 2)\n    3\n    """\n'
TESTS = "```python\nimport unittest\nclass T(unittest.TestCase):\n    def test_add(self):\n        self.assertEqual(add(1, 2), 3)\n        self.assertEqual(add(2, 2), 4)\n```"
CODE = "```python\ndef add(a, b):\n    return a + b\n```"
BAD = "```python\ndef add(a, b):\n    return a - b\n```"


def test_task_from_class_source_keeps_frame_and_decorators():
    t = task_from_source(CLASS_SRC)
    assert (t.kind, t.entry) == ("class", "Calc")
    assert [m.name for m in t.methods] == ["add", "root"]  # __init__ is implemented: part of the frame
    assert t.methods[0].signature.startswith("@staticmethod")
    assert t.methods[1].examples.startswith(">>> Calc().root(9)")
    code = assemble(t, {"add": "def add(a, b):\n    return a + b", "root": "def root(self, x):\n    return math.sqrt(x)"})
    assert "@staticmethod" in code and "self.mem = 0" in code
    assert run_examples(t, code)["ok"] is True


def test_task_from_function_and_errors():
    t = task_from_source(FUNC_SRC)
    assert (t.kind, t.entry, t.methods[0].examples) == ("function", "add", ">>> add(1, 2)\n3")
    with pytest.raises(TaskSourceError):
        task_from_source("def f(:")
    with pytest.raises(TaskSourceError):
        task_from_source("def f(x):\n    return x\n")


def test_check_team_json_accepts_text_and_reports_conditions():
    assert check_team_json(json.dumps(REF))["admitted"] is True
    res = check_team_json((TEAMS / "devteam_proposal.json").read_text())
    assert res["violated"] == ["W1", "W2", "W4", "W5", "W6"]
    assert check_team_json("{not json")["diagnostics"][0]["cond"] == "W1"


def _fill(ws: AutoWorkspace, answers: dict[str, str]) -> None:
    for _ in range(5):
        batch = ws.next_bindings(limit=10)["bindings"]
        if not batch:
            return
        for b in batch:
            res = ws.submit_binding(b["target_key"], b["binding"], answers[b["agent"]], b["footprint_version"])
            assert res["status"] == "accepted", res


def test_host_mode_end_to_end_with_persistence(tmp_path: Path):
    ws = AutoWorkspace(tmp_path, backend="host")
    with pytest.raises(AutoWorkspaceError, match="no task yet"):
        ws.builder_prompt()
    ws.set_task(FUNC_SRC)
    assert ws.builder_prompt()["mode"] == "propose"
    rej = ws.submit_team(json.loads((TEAMS / "devteam_proposal.json").read_text()))
    assert rej["admitted"] is False
    rev = ws.builder_prompt()
    assert rev["mode"] == "revise" and "W6" in rev["prompt"]
    ok = ws.submit_team(REF)
    assert ok["admitted"] and ok["mode"] == "admitted"
    run = ws.run()
    assert (run["pending"], run["blocked_on_upstream"], run["phi"]) == (1, 1, False)
    # the Tester's value first; the Developer's prompt then carries the accepted tests
    (b,) = ws.next_bindings()["bindings"]
    assert b["agent"] == "Tester"
    assert ws.submit_binding(b["target_key"], b["binding"], "```python\nprint(1)\n```", b["footprint_version"])["status"] == "rejected"
    assert ws.submit_binding(b["target_key"], b["binding"], TESTS, b["footprint_version"])["status"] == "accepted"
    # a fresh instance (another process) resumes from disk
    ws2 = AutoWorkspace(tmp_path, backend="host")
    (b,) = ws2.next_bindings()["bindings"]
    assert b["agent"] == "Developer" and "assertEqual(add(2, 2), 4)" in b["prompt"]
    assert ws2.submit_binding(b["target_key"], b["binding"], BAD, b["footprint_version"])["status"] == "rejected"
    assert ws2.submit_binding(b["target_key"], b["binding"], CODE, b["footprint_version"])["status"] == "accepted"
    st = AutoWorkspace(tmp_path, backend="host").status()
    assert st["phi"] is True and st["admission_rounds"] == 2
    d = ws2.deliverable()
    assert d["complete"] and "return a + b" in d["code"]
    # an unchanged resubmission is a no-op; a prompt change is an in-place delta keeping the tests
    assert ws2.submit_team(REF)["mode"] == "noop"
    changed = copy.deepcopy(REF)
    changed["handoffs"][1]["rules"][0]["llm"][0]["prompt"] = "Implement this function; keep it short."
    delta = ws2.submit_team(changed)
    assert delta["mode"] == "in-place" and delta["kept_values"] == 1
    run = ws2.run()
    assert run["phi"] is False and run["pending"] == 1 and run["pending_preview"][0]["agent"] == "Developer"
    _fill(ws2, {"Developer": CODE})
    assert ws2.run()["phi"] is True


def test_host_mode_attribution_locates_escalation(tmp_path: Path):
    ws = AutoWorkspace(tmp_path, backend="host", k=2)
    ws.set_task(FUNC_SRC)
    ws.submit_team(REF)
    (b,) = ws.next_bindings()["bindings"]
    ws.submit_binding(b["target_key"], b["binding"], TESTS, b["footprint_version"])
    (b,) = ws.next_bindings()["bindings"]
    for _ in range(2):
        last = ws.submit_binding(b["target_key"], b["binding"], BAD, b["footprint_version"])
    assert last["status"] == "escalated"
    run = ws.run()
    assert run["phi"] is False and run["pending"] == 0 and "noEsc" in run["open_clauses"]
    att = ws.attribute()
    loc = next(f for f in att["faults"] if f["clause"] == "noEsc")
    assert (loc["rule"], loc["binding"], loc["agent"]) == ("Method2Impl", "code", "Developer")
    assert ws.builder_prompt()["mode"] == "delta"
    assert "Method2Impl" in ws.builder_prompt("delta")["prompt"]
    # best effort deliverable is the last rejected value
    d = ws.deliverable()
    assert d["accepted"] == {"add": False} and "a - b" in d["code"]


class Scripted(LLMBackend):
    """Builder returns the reference team; bindings get tests / code."""
    name = "scripted"

    def generate(self, prompt, *, temperature=0.2, format=None, **kw):
        if format is not None:
            return json.dumps(REF)
        return TESTS if "Write unit tests" in prompt else CODE


def test_unattended_solve_with_engine_llm(tmp_path: Path):
    ws = AutoWorkspace(tmp_path, llm=Scripted())
    out = ws.solve(FUNC_SRC)
    assert out["status"] == "done" and out["phi"] is True
    assert "return a + b" in out["code"]
    st = AutoWorkspace(tmp_path, llm=Scripted()).status()
    assert st["team"]["agents"] == ["Tester", "Developer"]
    with pytest.raises(AutoWorkspaceError, match="engine LLM"):
        AutoWorkspace(tmp_path, backend="host").solve()


def test_examples_with_statements_echo_like_doctest():
    """`s.push(2); s.pop()` is a statement line: its last value must be echoed (doctest 'single' mode)."""
    src = ('class S:\n    def __init__(self):\n        self.v = []\n\n    def push(self, x):\n        """>>> s = S()\n'
           '        >>> s.push(2); s.pop()\n        2\n        """\n        pass\n\n    def pop(self):\n        """Pop."""\n        pass\n')
    t = task_from_source(src)
    good = {"push": "def push(self, x):\n    self.v.append(x)", "pop": "def pop(self):\n    return self.v.pop()"}
    assert run_examples(t, assemble(t, good))["ok"] is True
    bad = dict(good, pop="def pop(self):\n    return None")
    assert run_examples(t, assemble(t, bad))["failed"][0]["got"] == ""
