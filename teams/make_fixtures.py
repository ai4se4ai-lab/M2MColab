"""Writes the hand-written typed teams used by the paper's examples and the
checker study (RQ2 mutation analysis).

  devteam_proposal.json     Listing 5: the illustrative first proposal, one defect of each class D1-D5
  devteam_admitted.json     Listing 4: the admitted typed DevTeam, G1 (only checked obligations)
  devteam_admitted_g2.json  same, G2 (methods must also be delivered)
  reqteam_pilot.json        a requirements team (user stories -> API operations, tests, code)
                            whose code is never behaviourally checked (rejected by W4)
  reqteam_admitted.json     the requirements team after one checked delta, G1
  reqteam_admitted_g2.json  same, G2 (accepted stories must also be delivered)
  classeval_reference.json  the Typed-Ref condition: the DevTeam (Architect, Tester, Developer), G2
  pair_team.json            a minimal admitted team (Tester, Developer) for the service and plugin tests

Run:  python teams/make_fixtures.py
"""
from __future__ import annotations

import copy
import json
from pathlib import Path

HERE = Path(__file__).parent
DONE = ["cover(G)", "valid", "fresh", "noEsc"]
GOAL_REF = {"type": "Goal.Method", "required": True}


# --------------------------------------------------------------------------
# the typed DevTeam of the running example (goal view: Lift's MM0)
# --------------------------------------------------------------------------

def devteam_admitted(g2: bool = False) -> dict:
    """Listing 4: Architect -> contract, Tester -> unit tests, Developer -> body."""
    t = {
        "name": "devteam",
        "goal_view": "Goal",
        "agents": [
            {"name": "Architect", "role": "You state, for one method, a precise contract: inputs, result, side effects on "
                                          "the object's fields and the errors it raises.", "tools": []},
            {"name": "Tester", "role": "You turn one method's documentation and examples into executable unittest tests "
                                       "with concrete expected values.", "tools": ["exec"]},
            {"name": "Developer", "role": "You implement one method of the class so that it meets its contract and "
                                          "passes its tests.", "tools": ["exec"]},
        ],
        "views": {
            "Design": {"classes": {"MethodDesign": {"attributes": {"name": "string", "contract": "string"},
                                                    "references": {"method": dict(GOAL_REF)}}}},
            "Test": {"classes": {"TestCase": {"attributes": {"name": "string", "code": "string"},
                                              "references": {"method": dict(GOAL_REF)}}}},
            "Code": {"classes": {"MethodImpl": {"attributes": {"name": "string", "body": "string"},
                                                "references": {"method": dict(GOAL_REF)}}}},
        },
        "writes": {"Architect": ["Design"], "Tester": ["Test"], "Developer": ["Code"]},
        "handoffs": [
            {"name": "Method2Design", "sources": ["Goal"], "target": "Design", "rules": [{
                "name": "Method2Design", "from": [{"var": "m", "type": "Goal!Method"}], "guard": None,
                "to": {"var": "d", "type": "Design!MethodDesign"}, "bind": {"name": "m.name", "method": "m"},
                "llm": [{"feature": "contract", "prompt": "State a contract",
                         "footprint": ["m.name", "m.signature", "m.docstring"],
                         "validator": [{"id": "nonempty"}]}]}]},
            {"name": "Method2Test", "sources": ["Goal"], "target": "Test", "rules": [{
                "name": "Method2Test", "from": [{"var": "m", "type": "Goal!Method"}], "guard": None,
                "to": {"var": "t", "type": "Test!TestCase"}, "bind": {"name": "m.name", "method": "m"},
                "llm": [{"feature": "code", "prompt": "Write unit tests for this method",
                         "footprint": ["m.signature", "m.docstring", "m.examples.call", "m.examples.expected"],
                         "validator": [{"id": "test_valid", "args": {"method": "m"}}]}]}]},
            {"name": "Design2Impl", "sources": ["Design", "Test"], "target": "Code", "rules": [{
                "name": "Design2Impl",
                "from": [{"var": "d", "type": "Design!MethodDesign"}, {"var": "t", "type": "Test!TestCase"}],
                "guard": "t.method = d.method",
                "to": {"var": "i", "type": "Code!MethodImpl"}, "bind": {"name": "d.name", "method": "d.method"},
                "llm": [{"feature": "body", "prompt": "Implement the method",
                         "footprint": ["d.method.signature", "d.contract", "t.code"],
                         "validator": [{"id": "compiles"}, {"id": "passes_tests", "args": {"tests": "t.code"}}]}]}]},
        ],
        "goal": [{"class": "Example", "scope": "all", "mode": "checked", "anchors": ["call", "expected"]}],
        "deliverable": {"view": "Code", "class": "MethodImpl", "feature": "body"},
        "done": list(DONE),
    }
    if g2:
        t["goal"].append({"class": "Method", "scope": "all", "mode": "delivered"})
    return t


def devteam_proposal() -> dict:
    """Listing 5: reads well, but contains one instance of each defect."""
    t = devteam_admitted(g2=True)
    t["agents"][1]["tools"] = []  # D5: the Tester has no exec ...
    t["writes"] = {"Architect": ["Design"], "Tester": ["Test"], "Developer": ["Code", "Test"]}  # D3
    t["views"]["Test"] = {"classes": {"TestRun": {"attributes": {"verdict": "string"}}}}
    t["handoffs"] = [
        {"name": "Method2Design", "sources": ["Goal"], "target": "Design", "rules": [{
            "name": "Method2Design", "from": [{"var": "m", "type": "Goal!Method"}],
            "to": {"var": "d", "type": "Design!MethodDesign"}, "bind": {"name": "m.name", "method": "m"},
            "llm": [{"feature": "contract", "prompt": "State a contract", "footprint": ["m.name", "m.signature"],
                     "validator": [{"id": "nonempty"}]}]}]},
        {"name": "Design2Impl", "sources": ["Design"], "target": "Code", "rules": [{
            "name": "Design2Impl", "from": [{"var": "d", "type": "Design!MethodDesign"}],
            "to": {"var": "i", "type": "Code!MethodImpl"}, "bind": {"name": "d.name", "method": "d.method"},
            "llm": [{"feature": "body", "prompt": "Implement the method",
                     "footprint": ["d.contract", "d.returnType"],  # D2: an undeclared feature
                     "validator": [{"id": "compiles"}]}]}]},
        {"name": "Impl2Run", "sources": ["Code"], "target": "Test", "rules": [{  # D1: no rule reads an Example
            "name": "Impl2Run", "from": [{"var": "i", "type": "Code!MethodImpl"}],
            "to": {"var": "r", "type": "Test!TestRun"}, "bind": {},
            "llm": [{"feature": "verdict", "prompt": "Run and judge", "footprint": ["i.body"],
                     "validator": [{"id": "smoke_test", "args": {"body": "i.body"}}]}]}]},  # ... but must run code
    ]
    t["done"] = list(DONE) + ["Tester.says('ALL TESTS PASS')"]  # D4
    return t


def pair_team() -> dict:
    """A minimal admitted team (Tester -> tests, Developer -> code) used by the
    host-mode, service and plugin tests."""
    return {
        "name": "pair_team",
        "goal_view": "Goal",
        "agents": [
            {"name": "Tester", "role": "You write unittest tests for one method from its documentation and examples.", "tools": ["exec"]},
            {"name": "Developer", "role": "You implement one method so that its tests pass.", "tools": ["exec"]},
        ],
        "views": {
            "Test": {"classes": {"MethodTest": {"attributes": {"name": "string", "code": "string"},
                                                "references": {"method": dict(GOAL_REF)}}}},
            "Code": {"classes": {"MethodImpl": {"attributes": {"name": "string", "code": "string"},
                                                "references": {"method": dict(GOAL_REF)}}}},
        },
        "writes": {"Tester": ["Test"], "Developer": ["Code"]},
        "handoffs": [
            {"name": "Goal2Test", "sources": ["Goal"], "target": "Test", "rules": [{
                "name": "Method2Test", "from": [{"var": "m", "type": "Goal!Method"}], "guard": None,
                "to": {"var": "t", "type": "Test!MethodTest"}, "bind": {"name": "m.name", "method": "m"},
                "llm": [{"feature": "code", "prompt": "Write unit tests for this method",
                         "footprint": ["m.signature", "m.docstring", "m.examples.call", "m.examples.expected"],
                         "validator": [{"id": "test_valid", "args": {"method": "m"}}]}]}]},
            {"name": "Test2Code", "sources": ["Test"], "target": "Code", "rules": [{
                "name": "Method2Impl", "from": [{"var": "t", "type": "Test!MethodTest"}], "guard": None,
                "to": {"var": "i", "type": "Code!MethodImpl"}, "bind": {"name": "t.name", "method": "t.method"},
                "llm": [{"feature": "code", "prompt": "Implement this method",
                         "footprint": ["t.method.signature", "t.method.docstring", "t.code"],
                         "validator": [{"id": "passes_tests", "args": {"tests": "t.code"}}]}]}]},
        ],
        "goal": [{"class": "Example", "scope": "all", "mode": "checked", "anchors": ["call", "expected"]},
                 {"class": "Method", "scope": "all", "mode": "delivered"}],
        "deliverable": {"view": "Code", "class": "MethodImpl", "feature": "code"},
        "done": list(DONE),
    }


def classeval_reference() -> dict:
    """The Typed-Ref condition: the admitted DevTeam (G2), hand-written."""
    t = devteam_admitted(g2=True)
    t["name"] = "typed_ref"
    return t


# --------------------------------------------------------------------------
# a requirements team: user stories -> API operations, tests and code
# --------------------------------------------------------------------------

REQ = {
    "classes": {
        "Epic": {"attributes": {"name": "string"}},
        "UserStory": {"attributes": {"id": "string", "title": "string", "status": "string"},
                      "references": {"criteria": {"type": "Req.Criterion", "many": True},
                                     "epic": {"type": "Req.Epic", "required": True}}},
        "Criterion": {"attributes": {"id": "string", "text": "string"},
                      "references": {"story": {"type": "Req.UserStory", "required": True}}},
    }
}


def reqteam_pilot() -> dict:
    """Hand-written pilot: components and operations from epics and stories,
    code checked only for compiling, one test oracle per criterion."""
    return {
        "name": "reqteam_pilot",
        "goal_view": "Req",
        "agents": [
            {"name": "Architect", "role": "Designs components and API operations.", "tools": []},
            {"name": "Developer", "role": "Implements operations.", "tools": ["exec"]},
            {"name": "Tester", "role": "Writes test oracles.", "tools": ["exec"]},
        ],
        "views": {
            "Req": copy.deepcopy(REQ),
            "Arch": {"classes": {
                "Component": {"attributes": {"name": "string"},
                              "references": {"epic": {"type": "Req.Epic", "required": True}}},
                "Operation": {"attributes": {"name": "string", "signature": "string"},
                              "references": {"component": {"type": "Arch.Component", "required": True},
                                             "story": {"type": "Req.UserStory", "required": True}}}}},
            "Code": {"classes": {"CodeEdit": {
                "attributes": {"file": "string", "body": "string"},
                "references": {"operation": {"type": "Arch.Operation", "required": True},
                               "story": {"type": "Req.UserStory", "required": True}}}}},
            "Test": {"classes": {"TestCase": {
                "attributes": {"name": "string", "oracle": "string"},
                "references": {"criterion": {"type": "Req.Criterion", "required": True}}}}},
        },
        "writes": {"Architect": ["Arch"], "Developer": ["Code"], "Tester": ["Test"]},
        "handoffs": [
            {"name": "Req2Arch", "sources": ["Req"], "target": "Arch", "rules": [
                {"name": "Epic2Component", "from": [{"var": "ep", "type": "Req!Epic"}],
                 "to": {"var": "c", "type": "Arch!Component"}, "bind": {"name": "ep.name", "epic": "ep"}, "llm": []},
                {"name": "Story2Operation", "from": [{"var": "s", "type": "Req!UserStory"}],
                 "guard": "s.status = 'accepted'", "to": {"var": "op", "type": "Arch!Operation"},
                 "bind": {"name": "s.title", "component": "s.epic", "story": "s"},
                 "llm": [{"feature": "signature", "prompt": "Derive an API signature",
                          "footprint": ["s.title", "s.criteria"], "validator": [{"id": "compiles"}]}]}]},
            {"name": "Arch2Code", "sources": ["Arch", "Req"], "target": "Code", "rules": [{
                "name": "Op2Edit", "from": [{"var": "op", "type": "Arch!Operation"}],
                "to": {"var": "e", "type": "Code!CodeEdit"},
                "bind": {"file": "op.name", "operation": "op", "story": "op.story"},
                "llm": [{"feature": "body", "prompt": "Implement the operation",
                         "footprint": ["op.signature", "op.story.criteria"], "validator": [{"id": "compiles"}]}]}]},
            {"name": "Req2Test", "sources": ["Req"], "target": "Test", "rules": [{
                "name": "Criterion2TestCase", "from": [{"var": "c", "type": "Req!Criterion"}],
                "guard": "c.story.status = 'accepted'", "to": {"var": "tc", "type": "Test!TestCase"},
                "bind": {"name": "c.id", "criterion": "c"},
                "llm": [{"feature": "oracle", "prompt": "Write a test oracle", "footprint": ["c.text"],
                         "validator": [{"id": "nonempty"}]}]}]},
        ],
        "goal": [{"class": "Criterion", "scope": "c.story.status = 'accepted'", "mode": "checked", "anchors": ["text"]}],
        "deliverable": {"view": "Code", "class": "CodeEdit", "feature": "body"},
        "done": list(DONE),
    }


def reqteam_admitted(g2: bool = False) -> dict:
    """One delta: tests are grouped per story, checked by execution, and the
    code body is checked by running them (behaviour validators)."""
    t = reqteam_pilot()
    t["name"] = "reqteam"
    t["views"]["Test"] = {"classes": {"TestSuite": {
        "attributes": {"name": "string", "code": "string"},
        "references": {"story": {"type": "Req.UserStory", "required": True}}}}}
    t["handoffs"][2] = {"name": "Req2Test", "sources": ["Req"], "target": "Test", "rules": [{
        "name": "Story2TestSuite", "from": [{"var": "s", "type": "Req!UserStory"}],
        "guard": "s.status = 'accepted'", "to": {"var": "ts", "type": "Test!TestSuite"},
        "bind": {"name": "s.id", "story": "s"},
        "llm": [{"feature": "code", "prompt": "Write one executable test per acceptance criterion",
                 "footprint": ["s.title", "s.criteria"], "validator": [{"id": "test_valid", "args": {"method": "s"}}]}]}]}
    code = t["handoffs"][1]
    code["sources"] = ["Arch", "Test", "Req"]
    rule = code["rules"][0]
    rule["from"].append({"var": "ts", "type": "Test!TestSuite"})
    rule["guard"] = "ts.story = op.story"
    rule["llm"][0]["footprint"] = ["op.signature", "op.story.criteria", "ts.code"]
    rule["llm"][0]["validator"] = [{"id": "passes_tests", "args": {"method": "op.story", "tests": "ts.code"}}]
    t["handoffs"] = [t["handoffs"][0], t["handoffs"][2], t["handoffs"][1]]
    if g2:
        t["goal"].append({"class": "UserStory", "scope": "s.status = 'accepted'", "mode": "delivered"})
    return t


def main() -> None:
    out = {
        "devteam_proposal.json": devteam_proposal(),
        "devteam_admitted.json": devteam_admitted(),
        "devteam_admitted_g2.json": devteam_admitted(g2=True),
        "reqteam_pilot.json": reqteam_pilot(),
        "reqteam_admitted.json": reqteam_admitted(),
        "reqteam_admitted_g2.json": reqteam_admitted(g2=True),
        "classeval_reference.json": classeval_reference(),
        "pair_team.json": pair_team(),
    }
    for name, team in out.items():
        (HERE / name).write_text(json.dumps(team, indent=2) + "\n")
        print("wrote", HERE / name)


if __name__ == "__main__":
    main()
