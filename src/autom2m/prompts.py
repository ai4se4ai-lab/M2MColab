"""Prompts for the typed-team builder (Steps A, C, F)."""
from __future__ import annotations

import json
from pathlib import Path

from .compile import GOAL_VIEW
from .examples import EXAMPLE_TASK, typed_example_text
from .vlib import catalogue

_EXAMPLE_PATH = Path(__file__).resolve().parents[3] / "teams" / "devteam_admitted_g2.json"


def example_team() -> str:
    try:
        return json.dumps(json.loads(_EXAMPLE_PATH.read_text()), separators=(",", ":"))
    except OSError:  # installed without the repo's teams/ directory
        return "{}"


FORMAT = """A typed team is ONE JSON object with these keys:
- "name": string
- "goal_view": "Goal"  (fixed; the task is lifted into it, see below; do not change it)
- "agents": [{"name", "role" (how the agent thinks, prose), "tools": subset of ["exec"]}]
- "views": {ViewName: {"classes": {ClassName: {"attributes": {attr: "string" | "string?"(optional)},
                                                "references": {ref: {"type": "View.Class", "required": bool, "many": bool}}}}}}
   Every view other than Goal is the form one agent fills in. Include the Goal view exactly as given.
- "writes": {AgentName: [ViewName]}  (who writes which view)
- "handoffs": [{"name", "sources": [ViewName], "target": ViewName, "rules": [Rule]}]
   Rule = {"name", "from": [{"var", "type": "View!Class"}], "guard": OCL text or null (e.g. "t.method = m"),
           "to": {"var", "type": "View!Class"},
           "bind": {feature: navigation path like "m.name" or "m", or a quoted literal},
           "llm": [{"feature", "prompt", "footprint": [navigation paths the LLM may read],
                    "validator": [{"id", "args": {param: navigation path}}]}]}
   For every object matching "from" (and the guard), the engine creates one "to" object, fills "bind"
   deterministically and asks an LLM for each "llm" feature, showing it ONLY the footprint.
- "goal": [{"class": GoalClass, "scope": "all", "kind": "checked" | "delivered"}]
- "deliverable": {"view", "class", "feature", "for": reference to the Goal class it delivers}
- "done": ["cover(G)", "valid", "fresh", "noObl"]   (the engine decides completion)
"""

RULES = """The team is admitted only if all of the following hold:
W1 every rule reads only its hand-off's source views and writes only its target view; every bound feature
   exists; every footprint/argument path navigates existing features; LLM features are attributes and each
   has at least one validator from the library with correctly typed arguments.
W2 every view other than Goal has exactly one writing agent; each class is created by exactly one rule.
W3 every attribute/reference not marked optional ("?") or required:false is bound exactly once by each rule creating it.
W4 every goal obligation flows, through footprints or validator inputs, into a value checked by a
   behaviour validator; the deliverable is checked by a behaviour validator and derived from its goal
   element; every view is reachable from Goal and leads to a behaviour check.
W5 "done" is exactly the engine clauses; the hand-off graph is acyclic.
W6 an agent that writes a view owns every validator's tools for the LLM features of that view.
"""


def goal_view_text() -> str:
    return json.dumps({"Goal": GOAL_VIEW}, separators=(",", ":"))


def propose_prompt(task_text: str, kind: str) -> str:
    unit = "a Python class; each Method object is one method of it" if kind == "class" else \
        "a single Python function; there is one Method object, the function itself"
    return f"""You are a team builder. Design a team of LLM agents that solves the programming task below.
Instead of prose, output a TYPED TEAM in the JSON format described here.

{FORMAT}
The task has already been lifted into the Goal view (fixed metamodel):
{goal_view_text()}
Goal contains one Task object and one Method object per method to implement ({unit}).
The deliverable must be the Python source of each Method.

Validator library (the only validators allowed):
{catalogue()}

{RULES}
Example task:
{EXAMPLE_TASK}
An admitted typed team for the example task ("Goal" stands for the fixed Goal view above):
{typed_example_text()}

TASK:
{task_text}

Output only the JSON object of the typed team."""


def revise_prompt(team_json: str, diagnostics: list[str]) -> str:
    diag = "\n".join(diagnostics)
    return f"""You are a team builder. The deterministic checker REJECTED your typed team.

{FORMAT}
Validator library:
{catalogue()}

{RULES}
Your team:
{team_json}

Checker diagnostics (each must be fixed):
{diag}

Output only the corrected JSON object of the full typed team."""


def delta_prompt(team_json: str, reports: list[str]) -> str:
    rep = "\n".join(reports)
    return f"""You are a team builder. Your admitted typed team ran, but the engine's acceptance predicate failed.
Attribution located the faults below. Propose a minimal change to the team that removes them
(e.g. widen a footprint, add a rule, move a binding, change a prompt or validator). Keep everything that
works unchanged: accepted values outside the changed parts are kept.

{FORMAT}
Validator library:
{catalogue()}

{RULES}
Current team:
{team_json}

Fault reports:
{rep}

Output only the JSON object of the full revised typed team."""
