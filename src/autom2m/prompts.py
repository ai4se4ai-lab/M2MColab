"""Builder prompts (component 2): Propose (step A), Revise from diagnostics
(step C) and the delta proposal of a repair (step F). Each restates the JSON
format of a typed team, the fixed goal view, the validator library and a
plain-language version of W1-W6, and shows the worked examples."""
from __future__ import annotations

import json

from .examples import typed_examples_text
from .lift import GOAL_VIEW
from .vlib import LIBRARY_CLAUSES, TOOLS, catalogue

FORMAT = f"""A typed team is ONE JSON object with these keys:
- "name": string
- "goal_view": "Goal"  (fixed: the task is lifted into it, see below; never change it)
- "agents": [{{"name", "role" (how the agent thinks, prose), "tools": subset of {list(TOOLS)}}}]
- "views": {{ViewName: {{"classes": {{ClassName: {{"attributes": {{attr: "string" | "string?" (optional)}},
                                                "references": {{ref: {{"type": "View.Class", "required": bool, "many": bool}}}}}}}}}}}}
   Every view other than Goal is the form exactly one agent fills in. Do NOT include the Goal view: it is fixed
   and added automatically.
- "writes": {{AgentName: [ViewName]}}  (who writes which view)
- "handoffs": [{{"name", "sources": [ViewName], "target": ViewName, "rules": [Rule]}}]
   Rule = {{"name", "from": [{{"var", "type": "View!Class"}}], "guard": null or an equality such as "t.method = d.method",
           "to": {{"var", "type": "View!Class"}},
           "bind": {{feature: a navigation path such as "m.name" or "m" or "d.method", or a quoted literal}},
           "llm": [{{"feature", "prompt", "footprint": [navigation paths the LLM may read],
                    "validator": [{{"id", "args": {{param: navigation path}}}}]}}]}}
   For every match of "from" (that satisfies the guard) the engine creates one "to" object, fills "bind"
   deterministically and asks the owning agent's LLM for each "llm" feature, showing it ONLY the footprint.
   Paths may navigate references, also into the Goal view (e.g. "d.method.examples.call").
- "goal": [{{"class": GoalClass, "scope": "all", "mode": "checked" | "delivered", "anchors": [features of the class]}}]
   checked: the anchor features must reach a value checked by a behaviour validator, through its footprint AND its
   validator; delivered: every such object must get a deliverable.
- "deliverable": {{"view", "class", "feature"}}  (the final product, one per goal Method)
- "done": ["cover(G)", "valid", "fresh", "noEsc"] (+ optionally {list(LIBRARY_CLAUSES)}): the engine decides completion
"""

RULES = """The deterministic checker admits the team only if all of these hold:
W1 every rule reads only its hand-off's source views (and the Goal view) and writes only declared features of its
   target class; every footprint, binding, guard and validator path navigates declared features; LLM features are
   attributes, each with at least one library validator with correctly typed arguments; no guard or "bind" reads a
   feature that an LLM writes.
W2 every view other than Goal has exactly one writing agent; each class is created by exactly one rule.
W3 every attribute/reference not marked optional is bound exactly once by the rule creating it; references resolve.
W4 every "checked" goal is anchored: its anchor features flow, through footprints (production) and through a
   validator's arguments (check), into the same behaviour-checked value; every "delivered" goal reaches the
   deliverable; every view is reachable from Goal and leads to a behaviour check (no idle or unverified agent).
W5 "done" contains the four engine clauses and nothing an agent claims; the hand-off graph is acyclic.
W6 an agent that writes a view has every tool the validators of its LLM features need.
"""


def goal_view_text() -> str:
    return json.dumps({"Goal": GOAL_VIEW}, separators=(",", ":"))


def propose_prompt(task_text: str, kind: str, *, n_examples: int = 3) -> str:
    unit = ("a Python class; each Method object is one method of it" if kind == "class" else
            "a single Python function; there is one Method object, the function itself")
    return f"""You are a team builder. Design a team of LLM agents that solves the programming task below.
Instead of prose role descriptions, output a TYPED TEAM in the JSON format described here.

{FORMAT}
The task is already lifted into the Goal view (fixed metamodel):
{goal_view_text()}
Goal contains one Task (with the class outline), one Method per method to implement ({unit}), and one Example per
documented example (call and expected result). Method.docstring does NOT contain the examples.
The deliverable must be the Python source of each Method.

Validator library (the only validators allowed):
{catalogue()}

{RULES}
Worked examples (each uses the fixed Goal view above, which is not repeated):
{typed_examples_text(n_examples)}

TASK:
{task_text}

Design a team that fits THIS task; do not copy an example. Output only the JSON object of the typed team."""


def revise_prompt(team_json: str, diagnostics: list[str]) -> str:
    diag = "\n".join(diagnostics)
    return f"""You are a team builder. The deterministic checker REJECTED your typed team.

{FORMAT}
Validator library:
{catalogue()}

{RULES}
Your team:
{team_json}

Checker diagnostics (fix every one; keep everything else):
{diag}

Output only the corrected JSON object of the full typed team."""


def delta_prompt(team_json: str, reports: list[str]) -> str:
    rep = "\n".join(reports)
    return f"""You are a team builder. Your admitted typed team ran, but the engine's acceptance predicate failed.
Attribution located the faults below. Propose a minimal change to the team that removes them (for example widen a
footprint, change a prompt or validator, add a rule or a view). Keep everything that works unchanged: accepted
values outside the changed parts are kept. The changed team is checked again before it is applied.

{FORMAT}
Validator library:
{catalogue()}

{RULES}
Current team:
{team_json}

Fault reports:
{rep}

Output only the JSON object of the full revised typed team."""
