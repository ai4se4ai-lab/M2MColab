"""The builder's worked examples (component 2, prompt library).

Three structurally different examples (2, 3 and 4 agents), none from either
benchmark, given to every builder condition in its own format: typed
conditions see the typed team, the prose conditions (Free, Critic) the same
team as roles and a plan, the Schema condition the roles. With one example
(the preliminary configuration of RQ2) builders see only the 3-agent one.
"""
from __future__ import annotations

import json

DONE = ["cover(G)", "valid", "fresh", "noEsc"]
GOALREF = {"type": "Goal.Method", "required": True}

# ---------------------------------------------------------------- example 1
TASK_SLUG = '''def slugify(text, sep="-"):
    """Lower-case `text`, keep letters and digits, and join the words with `sep`.
    >>> slugify("Hello, World!")
    'hello-world'
    >>> slugify("  A  b ", sep="_")
    'a_b'
    """
'''

TEAM_SLUG = {
    "name": "slug_team",
    "goal_view": "Goal",
    "agents": [
        {"name": "Tester", "role": "You turn a function's documentation and examples into unittest tests.", "tools": ["exec"]},
        {"name": "Developer", "role": "You implement the function so that its tests and examples pass.", "tools": ["exec"]},
    ],
    "views": {
        "Test": {"classes": {"FnTest": {"attributes": {"name": "string", "code": "string"},
                                        "references": {"method": dict(GOALREF)}}}},
        "Code": {"classes": {"FnImpl": {"attributes": {"name": "string", "body": "string"},
                                        "references": {"method": dict(GOALREF)}}}},
    },
    "writes": {"Tester": ["Test"], "Developer": ["Code"]},
    "handoffs": [
        {"name": "Goal2Test", "sources": ["Goal"], "target": "Test", "rules": [{
            "name": "Method2Test", "from": [{"var": "m", "type": "Goal!Method"}], "guard": None,
            "to": {"var": "t", "type": "Test!FnTest"}, "bind": {"name": "m.name", "method": "m"},
            "llm": [{"feature": "code", "prompt": "Write unit tests that assert every documented example.",
                     "footprint": ["m.signature", "m.docstring", "m.examples.call", "m.examples.expected"],
                     "validator": [{"id": "test_valid", "args": {"method": "m"}}]}]}]},
        {"name": "Test2Code", "sources": ["Test"], "target": "Code", "rules": [{
            "name": "Test2Impl", "from": [{"var": "t", "type": "Test!FnTest"}], "guard": None,
            "to": {"var": "i", "type": "Code!FnImpl"}, "bind": {"name": "t.name", "method": "t.method"},
            "llm": [{"feature": "body", "prompt": "Implement the function.",
                     "footprint": ["t.method.signature", "t.method.docstring", "t.method.examples.call",
                                   "t.method.examples.expected", "t.code"],
                     "validator": [{"id": "passes_tests", "args": {"tests": "t.code"}},
                                   {"id": "examples_run", "args": {"method": "t.method"}}]}]}]},
    ],
    "goal": [{"class": "Example", "scope": "all", "mode": "checked", "anchors": ["call", "expected"]},
             {"class": "Method", "scope": "all", "mode": "delivered"}],
    "deliverable": {"view": "Code", "class": "FnImpl", "feature": "body"},
    "done": list(DONE),
}

PROSE_SLUG = {
    "agents": [
        {"name": "Tester", "role": "Expert in unit testing. You write unittest tests for the function from its "
                                   "docstring and examples and run them.", "tools": ["exec"]},
        {"name": "Developer", "role": "Expert Python developer. You implement the function so that the tests and "
                                      "documented examples pass.", "tools": ["exec"]},
    ],
    "plan": ["Tester: write unittest tests for slugify from its docstring.",
             "Developer: write the function slugify in one python block.",
             "Tester: run the tests and report failures; Developer fixes them.",
             "When the tests pass, reply TERMINATE."],
}

# ---------------------------------------------------------------- example 2
TASK_METER = '''class ParkingMeter:
    """A coin-operated parking meter that tracks paid minutes."""

    def __init__(self, rate_per_hour):
        self.rate_per_hour = rate_per_hour
        self.minutes = 0

    def insert_coins(self, amount):
        """Add paid time for `amount` money units at the hourly rate; returns total paid minutes.
        >>> meter = ParkingMeter(2)
        >>> meter.insert_coins(1)
        30
        """

    def tick(self, minutes):
        """Consume parked minutes, never below zero; returns the remaining minutes.
        >>> meter.tick(10)
        20
        """
'''

TEAM_METER = {
    "name": "meter_team",
    "goal_view": "Goal",
    "agents": [
        {"name": "Designer", "role": "You decide, for one method, the algorithm, edge cases and the fields it reads or updates.", "tools": []},
        {"name": "Tester", "role": "You turn one method's documentation and examples into executable unittest tests.", "tools": ["exec"]},
        {"name": "Developer", "role": "You implement one method following its design so that its tests pass.", "tools": ["exec"]},
    ],
    "views": {
        "Design": {"classes": {"MethodPlan": {"attributes": {"name": "string", "plan": "string"},
                                              "references": {"method": dict(GOALREF)}}}},
        "Test": {"classes": {"MethodTest": {"attributes": {"name": "string", "code": "string"},
                                            "references": {"method": dict(GOALREF)}}}},
        "Code": {"classes": {"MethodImpl": {"attributes": {"name": "string", "code": "string"},
                                            "references": {"method": dict(GOALREF)}}}},
    },
    "writes": {"Designer": ["Design"], "Tester": ["Test"], "Developer": ["Code"]},
    "handoffs": [
        {"name": "Goal2Design", "sources": ["Goal"], "target": "Design", "rules": [{
            "name": "Method2Plan", "from": [{"var": "m", "type": "Goal!Method"}], "guard": None,
            "to": {"var": "d", "type": "Design!MethodPlan"}, "bind": {"name": "m.name", "method": "m"},
            "llm": [{"feature": "plan", "prompt": "Describe in a few bullet points how to implement this method.",
                     "footprint": ["m.task.outline", "m.signature", "m.docstring"], "validator": [{"id": "nonempty"}]}]}]},
        {"name": "Goal2Test", "sources": ["Goal"], "target": "Test", "rules": [{
            "name": "Method2Test", "from": [{"var": "m", "type": "Goal!Method"}], "guard": None,
            "to": {"var": "t", "type": "Test!MethodTest"}, "bind": {"name": "m.name", "method": "m"},
            "llm": [{"feature": "code", "prompt": "Write unit tests for this method.",
                     "footprint": ["m.task.outline", "m.signature", "m.docstring", "m.examples.call", "m.examples.expected"],
                     "validator": [{"id": "test_valid", "args": {"method": "m"}}]}]}]},
        {"name": "Build2Code", "sources": ["Design", "Test"], "target": "Code", "rules": [{
            "name": "Plan2Impl",
            "from": [{"var": "d", "type": "Design!MethodPlan"}, {"var": "t", "type": "Test!MethodTest"}],
            "guard": "t.method = d.method",
            "to": {"var": "i", "type": "Code!MethodImpl"}, "bind": {"name": "d.name", "method": "d.method"},
            "llm": [{"feature": "code", "prompt": "Implement this method of the class.",
                     "footprint": ["d.method.task.outline", "d.method.signature", "d.plan", "t.code"],
                     "validator": [{"id": "compiles"}, {"id": "passes_tests", "args": {"tests": "t.code"}}]}]}]},
    ],
    "goal": [{"class": "Example", "scope": "all", "mode": "checked", "anchors": ["call", "expected"]},
             {"class": "Method", "scope": "all", "mode": "delivered"}],
    "deliverable": {"view": "Code", "class": "MethodImpl", "feature": "code"},
    "done": list(DONE),
}

PROSE_METER = {
    "agents": [
        {"name": "Designer", "role": "Expert in object-oriented design. You decide, for each method, the algorithm, "
                                     "edge cases and the fields it reads or updates.", "tools": []},
        {"name": "Tester", "role": "Expert in unit testing. You turn each method's documentation into executable "
                                   "unittest tests with concrete expected values, run them and report failures.", "tools": ["exec"]},
        {"name": "Developer", "role": "Expert Python developer. You implement the complete class following the design "
                                      "so that the tests and documented examples pass.", "tools": ["exec"]},
    ],
    "plan": ["Designer: describe the approach for insert_coins and tick.",
             "Tester: write unittest tests for insert_coins and tick from the docstrings.",
             "Developer: write the complete ParkingMeter class in one python block.",
             "Tester: run the tests against the class and report failures; Developer fixes them.",
             "When the tests pass, reply TERMINATE."],
}

# ---------------------------------------------------------------- example 3
TASK_INV = '''class Inventory:
    """Stock levels per item, with reservations."""

    def __init__(self):
        self.stock = {}

    def add(self, item, qty):
        """Increase the stock of `item` by `qty` (> 0); returns the new level.
        >>> inv = Inventory()
        >>> inv.add("pen", 5)
        5
        """

    def reserve(self, item, qty):
        """Take `qty` units of `item`; raises ValueError if fewer are in stock.
        >>> inv.reserve("pen", 2)
        3
        >>> inv.reserve("pen", 9)
        Traceback (most recent call last): ...
        ValueError: insufficient stock
        """
'''

TEAM_INV = {
    "name": "inventory_team",
    "goal_view": "Goal",
    "agents": [
        {"name": "Analyst", "role": "You list, for one method, its edge cases and error conditions.", "tools": []},
        {"name": "Architect", "role": "You state, for one method, a precise contract: inputs, result, side effects, errors.", "tools": []},
        {"name": "Tester", "role": "You write unittest tests for one method from its examples and edge cases.", "tools": ["exec"]},
        {"name": "Developer", "role": "You implement one method so that it meets its contract and passes its tests.", "tools": ["exec"]},
    ],
    "views": {
        "Analysis": {"classes": {"EdgeCases": {"attributes": {"name": "string", "cases": "string"},
                                               "references": {"method": dict(GOALREF)}}}},
        "Design": {"classes": {"Contract": {"attributes": {"name": "string", "text": "string"},
                                            "references": {"method": dict(GOALREF)}}}},
        "Test": {"classes": {"Suite": {"attributes": {"name": "string", "code": "string"},
                                       "references": {"method": dict(GOALREF)}}}},
        "Code": {"classes": {"Impl": {"attributes": {"name": "string", "body": "string"},
                                      "references": {"method": dict(GOALREF)}}}},
    },
    "writes": {"Analyst": ["Analysis"], "Architect": ["Design"], "Tester": ["Test"], "Developer": ["Code"]},
    "handoffs": [
        {"name": "Goal2Analysis", "sources": ["Goal"], "target": "Analysis", "rules": [{
            "name": "Method2Cases", "from": [{"var": "m", "type": "Goal!Method"}], "guard": None,
            "to": {"var": "a", "type": "Analysis!EdgeCases"}, "bind": {"name": "m.name", "method": "m"},
            "llm": [{"feature": "cases", "prompt": "List the edge cases and error conditions of this method.",
                     "footprint": ["m.signature", "m.docstring", "m.examples.call", "m.examples.expected"],
                     "validator": [{"id": "nonempty"}]}]}]},
        {"name": "Goal2Design", "sources": ["Goal"], "target": "Design", "rules": [{
            "name": "Method2Contract", "from": [{"var": "m", "type": "Goal!Method"}], "guard": None,
            "to": {"var": "c", "type": "Design!Contract"}, "bind": {"name": "m.name", "method": "m"},
            "llm": [{"feature": "text", "prompt": "State the contract of this method.",
                     "footprint": ["m.task.outline", "m.signature", "m.docstring"], "validator": [{"id": "nonempty"}]}]}]},
        {"name": "Analysis2Test", "sources": ["Analysis"], "target": "Test", "rules": [{
            "name": "Cases2Suite", "from": [{"var": "a", "type": "Analysis!EdgeCases"}], "guard": None,
            "to": {"var": "s", "type": "Test!Suite"}, "bind": {"name": "a.name", "method": "a.method"},
            "llm": [{"feature": "code", "prompt": "Write unit tests for the examples and the edge cases.",
                     "footprint": ["a.method.signature", "a.method.examples.call", "a.method.examples.expected", "a.cases"],
                     "validator": [{"id": "test_valid", "args": {"method": "a.method"}}]}]}]},
        {"name": "Build2Code", "sources": ["Design", "Test"], "target": "Code", "rules": [{
            "name": "Contract2Impl",
            "from": [{"var": "c", "type": "Design!Contract"}, {"var": "s", "type": "Test!Suite"}],
            "guard": "s.method = c.method",
            "to": {"var": "i", "type": "Code!Impl"}, "bind": {"name": "c.name", "method": "c.method"},
            "llm": [{"feature": "body", "prompt": "Implement this method of the class.",
                     "footprint": ["c.method.task.outline", "c.method.signature", "c.text", "s.code"],
                     "validator": [{"id": "passes_tests", "args": {"tests": "s.code"}},
                                   {"id": "examples_run", "args": {"method": "c.method"}}]}]}]},
    ],
    "goal": [{"class": "Example", "scope": "all", "mode": "checked", "anchors": ["call", "expected"]},
             {"class": "Method", "scope": "all", "mode": "delivered", "anchors": ["signature", "docstring"]}],
    "deliverable": {"view": "Code", "class": "Impl", "feature": "body"},
    "done": list(DONE) + ["examples_pass"],
}

PROSE_INV = {
    "agents": [
        {"name": "Analyst", "role": "Requirements analyst. You list each method's edge cases and error conditions.", "tools": []},
        {"name": "Architect", "role": "Software architect. You state each method's contract.", "tools": []},
        {"name": "Tester", "role": "Expert in unit testing. You write unittest tests for the examples and edge cases and run them.", "tools": ["exec"]},
        {"name": "Developer", "role": "Expert Python developer. You implement the complete class in one python block.", "tools": ["exec"]},
    ],
    "plan": ["Analyst: list the edge cases of add and reserve.",
             "Architect: state the contracts of add and reserve.",
             "Tester: write unittest tests for the examples and edge cases.",
             "Developer: write the complete Inventory class in one python block.",
             "Tester: run the tests and report failures; Developer fixes them.",
             "When the tests pass, reply TERMINATE."],
}

EXAMPLES = [  # (task, typed team, prose team)
    (TASK_SLUG, TEAM_SLUG, PROSE_SLUG),
    (TASK_METER, TEAM_METER, PROSE_METER),
    (TASK_INV, TEAM_INV, PROSE_INV),
]
SINGLE = [EXAMPLES[1]]  # the preliminary one-example configuration

# older names (one example)
EXAMPLE_TASK = TASK_METER
EXAMPLE_TYPED = TEAM_METER
EXAMPLE_PROSE = PROSE_METER


def chosen(n: int = 3):
    return EXAMPLES if n >= 3 else SINGLE


def typed_examples_text(n: int = 3) -> str:
    parts = []
    for i, (task, team, _prose) in enumerate(chosen(n), 1):
        parts.append(f"Example {i} task:\n{task}\nAn admitted typed team for example {i} "
                     f"({len(team['agents'])} agents):\n{json.dumps(team, separators=(',', ':'))}")
    return "\n\n".join(parts)


def prose_examples_text(n: int = 3) -> str:
    parts = []
    for i, (task, _team, prose) in enumerate(chosen(n), 1):
        parts.append(f"Example {i} task:\n{task}\nExample team for example {i}:\n{json.dumps(prose, indent=1)}")
    return "\n\n".join(parts)


def typed_example_text() -> str:  # older single-example accessor
    return json.dumps(TEAM_METER, separators=(",", ":"))


def prose_example_text() -> str:
    return json.dumps(PROSE_METER, indent=1)
