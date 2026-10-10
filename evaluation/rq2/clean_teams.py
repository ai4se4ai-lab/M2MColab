"""Hand-written typed teams for ClassEval classes, the hand-written half of
the RQ2 clean set. They are written from six team shapes with varied roles,
footprints and validators, without running the checker while writing them
(the checker's verdict on them is the false-alarm measurement).

    python -m evaluation.rq2.clean_teams     # prints the checker's verdict on each
"""
from __future__ import annotations

import itertools

DONE = ["cover(G)", "valid", "fresh", "noEsc"]
REF = {"type": "Goal.Method", "required": True}
EX_OBL = {"class": "Example", "scope": "all", "mode": "checked", "anchors": ["call", "expected"]}
M_OBL = {"class": "Method", "scope": "all", "mode": "delivered"}


def _view(cls: str, attrs: list[str]) -> dict:
    return {"classes": {cls: {"attributes": {a: "string" for a in ["name"] + attrs}, "references": {"method": dict(REF)}}}}


def _rule(name, frm, to, bind, feature, prompt, footprint, validators, guard=None):
    return {"name": name, "from": frm, "guard": guard, "to": to, "bind": bind,
            "llm": [{"feature": feature, "prompt": prompt, "footprint": footprint, "validator": validators}]}


def pair(dev_validators: list, test_fp: list, dev_fp: list, label: str) -> dict:
    """Tester -> tests per method; Developer -> body from the tests."""
    return {
        "name": f"pair_{label}", "goal_view": "Goal",
        "agents": [{"name": "Tester", "role": "Writes unit tests for one method.", "tools": ["exec"]},
                   {"name": "Developer", "role": "Implements one method so that its tests pass.", "tools": ["exec"]}],
        "views": {"Test": _view("UnitTests", ["code"]), "Code": _view("Body", ["source"])},
        "writes": {"Tester": ["Test"], "Developer": ["Code"]},
        "handoffs": [
            {"name": "Goal2Test", "sources": ["Goal"], "target": "Test", "rules": [_rule(
                "MethodTests", [{"var": "m", "type": "Goal!Method"}], {"var": "u", "type": "Test!UnitTests"},
                {"name": "m.name", "method": "m"}, "code", "Write unit tests for this method.", test_fp,
                [{"id": "test_valid", "args": {"method": "m"}}])]},
            {"name": "Test2Code", "sources": ["Test"], "target": "Code", "rules": [_rule(
                "MethodBody", [{"var": "u", "type": "Test!UnitTests"}], {"var": "b", "type": "Code!Body"},
                {"name": "u.name", "method": "u.method"}, "source", "Implement this method.", dev_fp, dev_validators)]},
        ],
        "goal": [dict(EX_OBL), dict(M_OBL)],
        "deliverable": {"view": "Code", "class": "Body", "feature": "source"},
        "done": list(DONE),
    }


def trio(first: str, first_attr: str, dev_validators: list, label: str, join_on_test: bool = True) -> dict:
    """A planner (Architect / Designer / Analyst) + Tester + Developer (joined)."""
    first_view = {"Architect": "Design", "Designer": "Plan", "Analyst": "Analysis"}[first]
    first_cls = {"Architect": "Contract", "Designer": "Sketch", "Analyst": "Cases"}[first]
    dev_from = [{"var": "p", "type": f"{first_view}!{first_cls}"}, {"var": "u", "type": "Test!UnitTests"}]
    return {
        "name": f"trio_{label}", "goal_view": "Goal",
        "agents": [{"name": first, "role": f"Writes the {first_attr} of one method.", "tools": []},
                   {"name": "Tester", "role": "Writes unit tests for one method.", "tools": ["exec"]},
                   {"name": "Developer", "role": "Implements one method.", "tools": ["exec"]}],
        "views": {first_view: _view(first_cls, [first_attr]), "Test": _view("UnitTests", ["code"]),
                  "Code": _view("Body", ["source"])},
        "writes": {first: [first_view], "Tester": ["Test"], "Developer": ["Code"]},
        "handoffs": [
            {"name": f"Goal2{first_view}", "sources": ["Goal"], "target": first_view, "rules": [_rule(
                f"Method2{first_cls}", [{"var": "m", "type": "Goal!Method"}], {"var": "p", "type": f"{first_view}!{first_cls}"},
                {"name": "m.name", "method": "m"}, first_attr, f"Write the {first_attr} of this method.",
                ["m.task.outline", "m.signature", "m.docstring"], [{"id": "nonempty"}])]},
            {"name": "Goal2Test", "sources": ["Goal"], "target": "Test", "rules": [_rule(
                "MethodTests", [{"var": "m", "type": "Goal!Method"}], {"var": "u", "type": "Test!UnitTests"},
                {"name": "m.name", "method": "m"}, "code", "Write unit tests for this method.",
                ["m.signature", "m.docstring", "m.examples.call", "m.examples.expected"],
                [{"id": "test_valid", "args": {"method": "m"}}])]},
            {"name": "Build2Code", "sources": [first_view, "Test"], "target": "Code", "rules": [_rule(
                "MethodBody", dev_from, {"var": "b", "type": "Code!Body"}, {"name": "p.name", "method": "p.method"},
                "source", "Implement this method.",
                ["p.method.task.outline", "p.method.signature", f"p.{first_attr}", "u.code"], dev_validators,
                guard="u.method = p.method" if join_on_test else "p.method = u.method")]},
        ],
        "goal": [dict(EX_OBL), dict(M_OBL)],
        "deliverable": {"view": "Code", "class": "Body", "feature": "source"},
        "done": list(DONE),
    }


def chain(label: str, dev_validators: list) -> dict:
    """Analyst (edge cases) -> Tester (tests from examples and edge cases) -> Developer."""
    return {
        "name": f"chain_{label}", "goal_view": "Goal",
        "agents": [{"name": "Analyst", "role": "Lists one method's edge cases and errors.", "tools": []},
                   {"name": "Tester", "role": "Writes unit tests for one method.", "tools": ["exec"]},
                   {"name": "Developer", "role": "Implements one method.", "tools": ["exec"]}],
        "views": {"Analysis": _view("Cases", ["cases"]), "Test": _view("UnitTests", ["code"]),
                  "Code": _view("Body", ["source"])},
        "writes": {"Analyst": ["Analysis"], "Tester": ["Test"], "Developer": ["Code"]},
        "handoffs": [
            {"name": "Goal2Analysis", "sources": ["Goal"], "target": "Analysis", "rules": [_rule(
                "Method2Cases", [{"var": "m", "type": "Goal!Method"}], {"var": "a", "type": "Analysis!Cases"},
                {"name": "m.name", "method": "m"}, "cases", "List the edge cases of this method.",
                ["m.signature", "m.docstring", "m.examples.call", "m.examples.expected"], [{"id": "nonempty"}])]},
            {"name": "Analysis2Test", "sources": ["Analysis"], "target": "Test", "rules": [_rule(
                "Cases2Tests", [{"var": "a", "type": "Analysis!Cases"}], {"var": "u", "type": "Test!UnitTests"},
                {"name": "a.name", "method": "a.method"}, "code", "Write unit tests for the examples and edge cases.",
                ["a.method.signature", "a.method.examples.call", "a.method.examples.expected", "a.cases"],
                [{"id": "test_valid", "args": {"method": "a.method"}}])]},
            {"name": "Test2Code", "sources": ["Test"], "target": "Code", "rules": [_rule(
                "MethodBody", [{"var": "u", "type": "Test!UnitTests"}], {"var": "b", "type": "Code!Body"},
                {"name": "u.name", "method": "u.method"}, "source", "Implement this method.",
                ["u.method.task.outline", "u.method.signature", "u.method.docstring", "u.code"], dev_validators)]},
        ],
        "goal": [dict(EX_OBL), dict(M_OBL)],
        "deliverable": {"view": "Code", "class": "Body", "feature": "source"},
        "done": list(DONE),
    }


def refactor(label: str) -> dict:
    """Developer writes a first version from the goal; a Refactorer rewrites it
    from the code and the tests alone (its input already encodes the examples)."""
    t = pair([{"id": "passes_tests", "args": {"tests": "u.code"}}],
             ["m.signature", "m.docstring", "m.examples.call", "m.examples.expected"],
             ["u.method.signature", "u.method.docstring", "u.code"], label)
    t["name"] = f"refactor_{label}"
    t["agents"].append({"name": "Refactorer", "role": "Rewrites a method for clarity without changing behaviour.", "tools": ["exec"]})
    t["views"]["Clean"] = {"classes": {"Final": {"attributes": {"name": "string", "source": "string"},
                                                  "references": {"body": {"type": "Code.Body", "required": True}}}}}
    t["writes"]["Refactorer"] = ["Clean"]
    t["handoffs"].append({"name": "Code2Clean", "sources": ["Code", "Test"], "target": "Clean", "rules": [_rule(
        "Refactor", [{"var": "b", "type": "Code!Body"}, {"var": "u", "type": "Test!UnitTests"}],
        {"var": "f", "type": "Clean!Final"}, {"name": "b.name", "body": "b"}, "source",
        "Refactor this method without changing its behaviour.", ["b.source"],
        [{"id": "passes_tests", "args": {"tests": "u.code"}}], guard="u.method = b.method")]})
    t["deliverable"] = {"view": "Clean", "class": "Final", "feature": "source"}
    return t


def hand_written() -> list[tuple[str, dict]]:
    pt = {"id": "passes_tests", "args": {"tests": "u.code"}}
    er = {"id": "examples_run", "args": {"method": "u.method"}}
    teams = []
    fps_t = [["m.signature", "m.docstring", "m.examples.call", "m.examples.expected"],
             ["m.task.outline", "m.signature", "m.docstring", "m.examples.call", "m.examples.expected"],
             ["m.name", "m.signature", "m.examples.call", "m.examples.expected"]]
    fps_d = [["u.method.signature", "u.method.docstring", "u.code"],
             ["u.method.task.outline", "u.method.signature", "u.code"],
             ["u.method.signature", "u.method.docstring", "u.method.examples.call", "u.method.examples.expected", "u.code"]]
    for i, (ft, fd, vs) in enumerate(itertools.islice(itertools.product(fps_t, fps_d, [[pt], [pt, er], [{"id": "compiles"}, pt]]), 12)):
        teams.append((f"pair{i}", pair(vs, ft, fd, str(i))))
    pt_trio = {"id": "passes_tests", "args": {"tests": "u.code"}}
    er_trio = {"id": "examples_run", "args": {"method": "p.method"}}
    for i, (first, attr) in enumerate([("Architect", "contract"), ("Designer", "plan"), ("Analyst", "edge_cases")]):
        for j, vs in enumerate([[pt_trio], [pt_trio, er_trio], [{"id": "compiles"}, pt_trio]]):
            teams.append((f"trio{i}{j}", trio(first, attr, vs, f"{i}{j}", join_on_test=j != 1)))
    for i, vs in enumerate([[pt], [pt, er], [{"id": "compiles"}, pt, er]]):
        teams.append((f"chain{i}", chain(str(i), vs)))
    for i in range(3):
        teams.append((f"refactor{i}", refactor(str(i))))
    for i, vs in enumerate([[pt], [pt, er], [{"id": "compiles"}, pt]]):
        t = pair(vs, fps_t[i], fps_d[i], f"x{i}")
        t["name"] = f"pairx_{i}"
        t["done"] = list(DONE) + ["examples_pass"]
        teams.append((f"pairx{i}", t))
    return teams[:30]


def main() -> int:
    from autom2m.checker import check

    for name, t in hand_written():
        r = check(t)
        print(f"{name:12} {'admitted' if r.admitted else 'REJECTED ' + str(sorted(r.conds()))}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
