"""Team sketches (AutoM2M-Lean, builder v3).

A sketch is the design part of a typed team only:

  {"name": str,
   "agents": [{"name", "role", "writes": [View]}],
   "views":  {View: {Class: [attribute names]}},
   "rules":  [{"name", "from": [{"var", "type": "View!Class"}], "to": {"var", "type": "View!Class"},
               "llm": [{"feature", "prompt", "footprint": [paths], "validator": [validator ids]}]}]}

Everything else (references to the goal method, hand-offs and their source
views, structural bindings, join guards, validator arguments, tools, goal
obligations, deliverable, acceptance predicate) is wiring and is computed by
wiring inference (wiring.py). `expand` turns a sketch into the typed-team
JSON that inference completes; `to_sketch` renders an admitted team as a
sketch (for the builder's worked examples).
"""
from __future__ import annotations

import json
from collections import OrderedDict
from typing import Any

from .examples import chosen
from .lift import GOAL_VIEW_NAME
from .vlib import VLIB

_STR = {"type": "string"}


def _arr(items: dict) -> dict:
    return {"type": "array", "items": items}


def _obj(props: dict) -> dict:
    return {"type": "object", "required": list(props), "properties": props}


SKETCH_SCHEMA: dict = _obj({
    "name": _STR,
    "agents": _arr(_obj({"name": _STR, "role": _STR, "writes": _arr(_STR)})),
    "views": {"type": "object", "additionalProperties": {"type": "object", "additionalProperties": _arr(_STR)}},
    "rules": _arr(_obj({
        "name": _STR,
        "from": _arr(_obj({"var": _STR, "type": _STR})),
        "to": _obj({"var": _STR, "type": _STR}),
        "llm": _arr(_obj({"feature": _STR, "prompt": _STR, "footprint": _arr(_STR),
                          "validator": _arr({"type": "string", "enum": [k for k, v in VLIB.items() if v.offered]})})),
    })),
})


def is_sketch(data: Any) -> bool:
    return isinstance(data, dict) and isinstance(data.get("rules"), list) and "handoffs" not in data


def expand(sketch: dict) -> dict:
    """The typed-team JSON of a sketch (still unwired: inference completes it)."""
    if not is_sketch(sketch):
        return sketch
    views: dict = {}
    for v, classes in (sketch.get("views") or {}).items():
        if not isinstance(classes, dict) or v == GOAL_VIEW_NAME:
            continue
        views[str(v)] = {"classes": {}}
        for c, attrs in classes.items():
            names = attrs if isinstance(attrs, list) else list(attrs) if isinstance(attrs, dict) else []
            views[str(v)]["classes"][str(c)] = {"attributes": {str(a): "string" for a in names if str(a).strip()},
                                                "references": {}}
    agents, writes = [], {}
    for a in sketch.get("agents") or []:
        if not isinstance(a, dict) or not a.get("name"):
            continue
        agents.append({"name": str(a["name"]), "role": str(a.get("role", "")), "tools": []})
        w = a.get("writes")
        writes[str(a["name"])] = [str(x) for x in (w if isinstance(w, list) else [w] if w else [])]
    # one hand-off per target view, reading the views its rules read
    hands: "OrderedDict[str, dict]" = OrderedDict()
    for r in sketch.get("rules") or []:
        if not isinstance(r, dict):
            continue
        to = r.get("to") if isinstance(r.get("to"), dict) else {}
        tv = str(to.get("type", "")).replace(".", "!").split("!")[0]
        srcs = []
        for s in r.get("from") or []:
            if isinstance(s, dict):
                sv = str(s.get("type", "")).replace(".", "!").split("!")[0]
                if sv and sv not in srcs:
                    srcs.append(sv)
        h = hands.setdefault(tv, {"name": "", "sources": [], "target": tv, "rules": []})
        h["sources"] = list(dict.fromkeys(h["sources"] + srcs))
        llm = []
        for b in r.get("llm") or []:
            if isinstance(b, dict):
                vals = b.get("validator") or []
                vals = vals if isinstance(vals, list) else [vals]
                llm.append({"feature": b.get("feature"), "prompt": b.get("prompt", ""),
                            "footprint": b.get("footprint") or [],
                            "validator": [{"id": v if isinstance(v, str) else (v or {}).get("id"), "args": {}}
                                          for v in vals]})
        h["rules"].append({"name": r.get("name"), "from": r.get("from") or [], "guard": None, "to": to,
                           "bind": {}, "llm": llm})
    for tv, h in hands.items():
        h["name"] = f"{'_'.join(h['sources']) or GOAL_VIEW_NAME}2{tv}"
    return {"name": sketch.get("name", "team"), "goal_view": GOAL_VIEW_NAME, "agents": agents, "views": views,
            "writes": writes, "handoffs": list(hands.values()), "goal": [], "deliverable": None, "done": []}


def to_sketch(team: dict) -> dict:
    """The sketch of a typed team (drops the wiring)."""
    writes = team.get("writes") or {}
    views = {}
    for v, spec in (team.get("views") or {}).items():
        if v == GOAL_VIEW_NAME:
            continue
        views[v] = {c: [a for a in (cs.get("attributes") or {})] for c, cs in (spec.get("classes") or {}).items()}
    rules = []
    for h in team.get("handoffs") or []:
        for r in h.get("rules") or []:
            rules.append({"name": r["name"], "from": r["from"], "to": r["to"],
                          "llm": [{"feature": b["feature"], "prompt": b["prompt"], "footprint": b["footprint"],
                                   "validator": [v["id"] for v in b.get("validator") or []]} for b in r.get("llm") or []]})
    return {"name": team.get("name", "team"),
            "agents": [{"name": a["name"], "role": a.get("role", ""), "writes": writes.get(a["name"], [])}
                       for a in team.get("agents") or []],
            "views": views, "rules": rules}


def sketch_examples_text(n: int = 3) -> str:
    parts = []
    for i, (task, team, _prose) in enumerate(chosen(n), 1):
        parts.append(f"Example {i} task:\n{task}\nA team sketch for example {i} ({len(team['agents'])} agents):\n"
                     f"{json.dumps(to_sketch(team), separators=(',', ':'))}")
    return "\n\n".join(parts)
