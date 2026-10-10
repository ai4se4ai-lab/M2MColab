"""One worked example, given to every builder condition in its own format.

The example task (ParkingMeter) is not part of any benchmark. Typed
conditions see EXAMPLE_TYPED, prose conditions see EXAMPLE_PROSE, the schema
condition sees EXAMPLE_SCHEMA_ROLES; all describe the same three-agent team.
"""
from __future__ import annotations

import json

EXAMPLE_TASK = '''class ParkingMeter:
    """A coin-operated parking meter that tracks paid minutes."""

    def __init__(self, rate_per_hour):
        self.rate_per_hour = rate_per_hour
        self.minutes = 0

    def insert_coins(self, amount):
        """
        Add paid time for `amount` money units at the hourly rate.
        :param amount: float, money inserted, must be positive
        :return: int, total paid minutes
        >>> meter = ParkingMeter(2)
        >>> meter.insert_coins(1)
        30
        """

    def tick(self, minutes):
        """
        Consume parked minutes; never below zero.
        :return: int, remaining minutes
        >>> meter = ParkingMeter(2); meter.insert_coins(1)
        >>> meter.tick(10)
        20
        """
'''

EXAMPLE_TYPED = {
    "name": "parking_meter_team",
    "goal_view": "Goal",
    "agents": [
        {"name": "Designer", "role": "You decide, for one method, the algorithm, edge cases and the fields it reads or updates.", "tools": []},
        {"name": "Tester", "role": "You turn a method's documentation into executable unit tests with concrete expected values.", "tools": ["exec"]},
        {"name": "Developer", "role": "You implement one method following its design so that its tests and examples pass.", "tools": ["exec"]},
    ],
    "views": {
        "Goal": "<fixed, as given>",
        "Design": {"classes": {"MethodDesign": {"attributes": {"name": "string", "plan": "string"},
                                                "references": {"method": {"type": "Goal.Method", "required": True}}}}},
        "Test": {"classes": {"MethodTest": {"attributes": {"name": "string", "code": "string"},
                                            "references": {"method": {"type": "Goal.Method", "required": True}}}}},
        "Code": {"classes": {"MethodImpl": {"attributes": {"name": "string", "code": "string"},
                                            "references": {"method": {"type": "Goal.Method", "required": True}}}}},
    },
    "writes": {"Designer": ["Design"], "Tester": ["Test"], "Developer": ["Code"]},
    "handoffs": [
        {"name": "Goal2Design", "sources": ["Goal"], "target": "Design", "rules": [{
            "name": "Method2Design", "from": [{"var": "m", "type": "Goal!Method"}], "guard": None,
            "to": {"var": "d", "type": "Design!MethodDesign"}, "bind": {"name": "m.name", "method": "m"},
            "llm": [{"feature": "plan", "prompt": "Describe in a few bullet points how to implement this method: steps, edge cases, fields used.",
                     "footprint": ["m.task.skeleton", "m.signature", "m.docstring"], "validator": [{"id": "nonempty"}]}]}]},
        {"name": "Goal2Test", "sources": ["Goal"], "target": "Test", "rules": [{
            "name": "Method2Test", "from": [{"var": "m", "type": "Goal!Method"}], "guard": None,
            "to": {"var": "t", "type": "Test!MethodTest"}, "bind": {"name": "m.name", "method": "m"},
            "llm": [{"feature": "code", "prompt": "Write unit tests for this method.",
                     "footprint": ["m.task.skeleton", "m.name", "m.docstring", "m.examples"],
                     "validator": [{"id": "test_valid", "args": {"method": "m"}}]}]}]},
        {"name": "Build2Code", "sources": ["Goal", "Design", "Test"], "target": "Code", "rules": [{
            "name": "Method2Impl",
            "from": [{"var": "d", "type": "Design!MethodDesign"}, {"var": "t", "type": "Test!MethodTest"}],
            "guard": "t.method = d.method",
            "to": {"var": "i", "type": "Code!MethodImpl"}, "bind": {"name": "d.method.name", "method": "d.method"},
            "llm": [{"feature": "code", "prompt": "Implement this method of the class.",
                     "footprint": ["d.method.task.skeleton", "d.method.signature", "d.method.docstring", "d.plan", "t.code"],
                     "validator": [{"id": "examples_run", "args": {"method": "d.method"}},
                                   {"id": "passes_tests", "args": {"method": "d.method", "tests": "t.code"}}]}]}]},
    ],
    "goal": [{"class": "Method", "scope": "all", "kind": "checked"},
             {"class": "Method", "scope": "all", "kind": "delivered"}],
    "deliverable": {"view": "Code", "class": "MethodImpl", "feature": "code", "for": "method"},
    "done": ["cover(G)", "valid", "fresh", "noObl"],
}

EXAMPLE_PROSE = {
    "agents": [
        {"name": "Designer", "role": "Expert in object-oriented design. You decide, for each method, the algorithm, edge cases and the fields it reads or updates."},
        {"name": "Tester", "role": "Expert in unit testing. You turn each method's documentation into executable unittest tests with concrete expected values, run them and report failures."},
        {"name": "Developer", "role": "Expert Python developer. You implement the complete class following the design so that the tests and documented examples pass."},
    ],
    "plan": [
        "Designer: describe the approach for insert_coins and tick.",
        "Tester: write unittest tests for insert_coins and tick from the docstrings.",
        "Developer: write the complete ParkingMeter class in one python block.",
        "Tester: run the tests against the class and report failures; Developer fixes them.",
        "When the tests pass, reply TERMINATE.",
    ],
}


def typed_example_text() -> str:
    return json.dumps(EXAMPLE_TYPED, separators=(",", ":"))


def prose_example_text() -> str:
    return json.dumps(EXAMPLE_PROSE, indent=1)
