"""Writes the hand-written typed teams used by the checker study.

  devteam_proposal.json   illustrative first proposal with one defect of each class D1-D5
  devteam_admitted.json   corrected DevTeam (G1: criteria are checked obligations)
  devteam_admitted_g2.json  same, G2: user stories must also be delivered
  chakin_pilot.json       our encoding of the hand-written AgentM2M pilot team (DevBench chakin)
  chakin_repaired.json    the pilot after one checked delta (behavioural code validator), G1
  chakin_repaired_g2.json same, G2

Run:  python teams/make_fixtures.py
"""
from __future__ import annotations

import copy
import json
from pathlib import Path

HERE = Path(__file__).parent
DONE = ["cover(G)", "valid", "fresh", "noObl"]

REQ = {
    "classes": {
        "UserStory": {"attributes": {"id": "string", "title": "string", "status": "string"},
                      "references": {"criteria": {"type": "Req.Criterion", "many": True}}},
        "Criterion": {"attributes": {"id": "string", "text": "string"},
                      "references": {"story": {"type": "Req.UserStory", "required": True}}},
    }
}


def devteam_admitted(g2: bool = False) -> dict:
    t = {
        "name": "devteam",
        "goal_view": "Req",
        "agents": [
            {"name": "Analyst", "role": "Turns requests into user stories with acceptance criteria.", "tools": []},
            {"name": "Architect", "role": "Designs one API operation per accepted story.", "tools": []},
            {"name": "Developer", "role": "Implements each operation so that its story's tests pass.", "tools": ["exec"]},
            {"name": "Tester", "role": "Writes executable tests for each story's acceptance criteria.", "tools": ["exec"]},
        ],
        "views": {
            "Req": REQ,
            "Arch": {"classes": {"Operation": {
                "attributes": {"name": "string", "signature": "string"},
                "references": {"story": {"type": "Req.UserStory", "required": True}}}}},
            "Test": {"classes": {"TestSuite": {
                "attributes": {"name": "string", "code": "string"},
                "references": {"story": {"type": "Req.UserStory", "required": True}}}}},
            "Code": {"classes": {"CodeEdit": {
                "attributes": {"file": "string", "body": "string"},
                "references": {"operation": {"type": "Arch.Operation", "required": True},
                               "story": {"type": "Req.UserStory", "required": True}}}}},
        },
        "writes": {"Analyst": ["Req"], "Architect": ["Arch"], "Developer": ["Code"], "Tester": ["Test"]},
        "handoffs": [
            {"name": "Req2Arch", "sources": ["Req"], "target": "Arch", "rules": [{
                "name": "Story2Operation",
                "from": [{"var": "s", "type": "Req!UserStory"}], "guard": "s.status = 'accepted'",
                "to": {"var": "op", "type": "Arch!Operation"},
                "bind": {"name": "s.title", "story": "s"},
                "llm": [{"feature": "signature", "prompt": "Derive an API signature",
                         "footprint": ["s.title", "s.criteria"], "validator": [{"id": "compiles"}]}]}]},
            {"name": "Req2Test", "sources": ["Req"], "target": "Test", "rules": [{
                "name": "Story2TestSuite",
                "from": [{"var": "s", "type": "Req!UserStory"}], "guard": "s.status = 'accepted'",
                "to": {"var": "ts", "type": "Test!TestSuite"},
                "bind": {"name": "s.id", "story": "s"},
                "llm": [{"feature": "code", "prompt": "Write one executable test per acceptance criterion",
                         "footprint": ["s.title", "s.criteria"],
                         "validator": [{"id": "test_valid", "args": {"method": "s"}}]}]}]},
            {"name": "Arch2Code", "sources": ["Arch", "Test", "Req"], "target": "Code", "rules": [{
                "name": "Op2Edit",
                "from": [{"var": "op", "type": "Arch!Operation"}, {"var": "ts", "type": "Test!TestSuite"}],
                "guard": "ts.story = op.story",
                "to": {"var": "e", "type": "Code!CodeEdit"},
                "bind": {"file": "op.name", "operation": "op", "story": "op.story"},
                "llm": [{"feature": "body", "prompt": "Implement the operation",
                         "footprint": ["op.signature", "op.story.criteria"],
                         "validator": [{"id": "passes_tests", "args": {"method": "op.story", "tests": "ts.code"}}]}]}]},
        ],
        "goal": [{"class": "Criterion", "scope": "c.story.status = 'accepted'"}],
        "deliverable": {"view": "Code", "class": "CodeEdit", "feature": "body", "for": "story"},
        "done": list(DONE),
    }
    if g2:
        t["goal"].append({"class": "UserStory", "scope": "s.status = 'accepted'", "kind": "delivered"})
    return t


def devteam_proposal() -> dict:
    """One defect per class, as in the paper's illustrative listing."""
    t = devteam_admitted()
    t["agents"][3]["tools"] = []  # D5: Tester cannot execute
    t["writes"] = {"Analyst": ["Req"], "Architect": ["Arch"], "Developer": ["Code", "Test"], "Tester": ["Test"]}  # D3
    t["views"]["Test"] = {"classes": {"TestRun": {"attributes": {"name": "string", "verdict": "string"}}}}
    t["views"]["Code"]["classes"]["CodeEdit"]["references"].pop("story")
    t["deliverable"] = None
    t["handoffs"] = [
        t["handoffs"][0],
        {"name": "Arch2Code", "sources": ["Arch"], "target": "Code", "rules": [{
            "name": "Op2Edit", "from": [{"var": "op", "type": "Arch!Operation"}],
            "to": {"var": "e", "type": "Code!CodeEdit"}, "bind": {"file": "op.name", "operation": "op"},
            "llm": [{"feature": "body", "prompt": "Implement", "footprint": ["op.signature", "op.returnType"],  # D2
                     "validator": [{"id": "compiles"}]}]}]},
        {"name": "Code2Test", "sources": ["Code"], "target": "Test", "rules": [{  # D1: no rule reads Criterion
            "name": "Edit2TestRun", "from": [{"var": "e", "type": "Code!CodeEdit"}],
            "to": {"var": "r", "type": "Test!TestRun"}, "bind": {"name": "e.file"},
            "llm": [{"feature": "verdict", "prompt": "Run and judge", "footprint": ["e.body"],
                     "validator": [{"id": "runs"}]}]}]},
    ]
    t["handoffs"][0]["rules"][0]["llm"][0]["footprint"] = ["s.title"]
    t["done"] = list(DONE) + ["Tester.says('ALL TESTS PASS')"]  # D4
    return t


def chakin_pilot() -> dict:
    """Hand-written pilot team: Req2Arch, Arch2Code, Req2Test; parses /
    compiles validators on signatures and bodies, oracle validator on tests."""
    req = copy.deepcopy(REQ)
    req["classes"]["Epic"] = {"attributes": {"name": "string"}}
    req["classes"]["UserStory"]["references"]["epic"] = {"type": "Req.Epic", "required": True}
    return {
        "name": "chakin_pilot",
        "goal_view": "Req",
        "agents": [
            {"name": "Analyst", "role": "Requirements owner.", "tools": []},
            {"name": "Architect", "role": "Designs components and operations.", "tools": []},
            {"name": "Developer", "role": "Implements operations.", "tools": ["exec"]},
            {"name": "Tester", "role": "Writes test oracles.", "tools": ["exec"]},
        ],
        "views": {
            "Req": req,
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
        "writes": {"Analyst": ["Req"], "Architect": ["Arch"], "Developer": ["Code"], "Tester": ["Test"]},
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
                         "validator": [{"id": "test_valid", "args": {"method": "c"}}]}]}]},
        ],
        "goal": [{"class": "Criterion", "scope": "c.story.status = 'accepted'"}],
        "deliverable": {"view": "Code", "class": "CodeEdit", "feature": "body", "for": "story"},
        "done": list(DONE),
    }


def chakin_repaired(g2: bool = False) -> dict:
    """One delta: tests are grouped per story and the code body is checked
    by executing them (behavioural validator; the Developer has exec)."""
    t = chakin_pilot()
    t["name"] = "chakin_repaired"
    t["views"]["Test"] = {"classes": {"TestSuite": {
        "attributes": {"name": "string", "code": "string"},
        "references": {"story": {"type": "Req.UserStory", "required": True}}}}}
    t["handoffs"][2] = {"name": "Req2Test", "sources": ["Req"], "target": "Test", "rules": [{
        "name": "Story2TestSuite", "from": [{"var": "s", "type": "Req!UserStory"}],
        "guard": "s.status = 'accepted'", "to": {"var": "ts", "type": "Test!TestSuite"},
        "bind": {"name": "s.id", "story": "s"},
        "llm": [{"feature": "code", "prompt": "Write one test oracle per acceptance criterion",
                 "footprint": ["s.criteria"], "validator": [{"id": "test_valid", "args": {"method": "s"}}]}]}]}
    code = t["handoffs"][1]
    code["sources"] = ["Arch", "Test", "Req"]
    rule = code["rules"][0]
    rule["from"].append({"var": "ts", "type": "Test!TestSuite"})
    rule["guard"] = "ts.story = op.story"
    rule["llm"][0]["validator"] = [{"id": "passes_tests", "args": {"method": "op.story", "tests": "ts.code"}}]
    # Req2Test now precedes Arch2Code in the hand-off order
    t["handoffs"] = [t["handoffs"][0], t["handoffs"][2], t["handoffs"][1]]
    if g2:
        t["goal"].append({"class": "UserStory", "scope": "s.status = 'accepted'", "kind": "delivered"})
    return t


def main() -> None:
    out = {
        "devteam_proposal.json": devteam_proposal(),
        "devteam_admitted.json": devteam_admitted(),
        "devteam_admitted_g2.json": devteam_admitted(g2=True),
        "chakin_pilot.json": chakin_pilot(),
        "chakin_repaired.json": chakin_repaired(),
        "chakin_repaired_g2.json": chakin_repaired(g2=True),
    }
    for name, team in out.items():
        (HERE / name).write_text(json.dumps(team, indent=2) + "\n")
        print("wrote", HERE / name)


if __name__ == "__main__":
    main()
