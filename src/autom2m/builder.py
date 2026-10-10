"""Component 2, the Builder, in its checked form (builder "v2").

An LLM still proposes the team and the checker still decides; the builder
only uses the checker's diagnostics better:

  * schema-constrained decoding: the JSON Schema of a typed team is given to
    the model's structured-output decoder, so a proposal always has the shape
    of a typed team (it can still be ill-typed, incomplete, ...);
  * checker-ranked proposals: k proposals are sampled and checked (a check
    costs milliseconds); an admitted one is taken, otherwise the one with the
    fewest violations is revised;
  * localized repair: each diagnostic is mapped to the JSON fragments it
    concerns (a rule, a class, the write rights, the goal, ...); the reviser
    rewrites only those fragments, given a catalogue of the navigation paths
    that type-check in each rule, and the fragments are put back into the
    team deterministically. Parts that were not diagnosed are never
    regenerated, so a fix cannot be lost by rewriting the whole team.

Every candidate, proposed or repaired, is admitted only by the checker.
"""
from __future__ import annotations

import copy
import json
from typing import Any

from .lift import normalize
from .typed_team import TeamFormatError, parse_team, rule_env, type_path
from .vlib import LIBRARY_CLAUSES, TOOLS, VLIB

_STR = {"type": "string"}
_STRMAP = {"type": "object", "additionalProperties": {"type": "string"}}

def _obj(props: dict, required: list[str]) -> dict:
    return {"type": "object", "required": required, "properties": props}


def _arr(items: dict) -> dict:
    return {"type": "array", "items": items}


_AGENT = _obj({"name": _STR, "role": _STR, "tools": _arr({"type": "string", "enum": list(TOOLS)})},
              ["name", "role", "tools"])
_REF = _obj({"type": _STR, "required": {"type": "boolean"}, "many": {"type": "boolean"}}, ["type"])
_CLASS = _obj({"attributes": _STRMAP, "references": {"type": "object", "additionalProperties": _REF}}, ["attributes"])
_VIEW = _obj({"classes": {"type": "object", "additionalProperties": _CLASS}}, ["classes"])
_VALIDATOR = _obj({"id": {"type": "string", "enum": [k for k, v in VLIB.items() if v.offered]}, "args": _STRMAP}, ["id"])
_LLM = _obj({"feature": _STR, "prompt": _STR, "footprint": _arr(_STR), "validator": _arr(_VALIDATOR)},
            ["feature", "prompt", "footprint", "validator"])
_VAR = _obj({"var": _STR, "type": _STR}, ["var", "type"])
_RULE = _obj({"name": _STR, "from": _arr(_VAR), "guard": {"type": ["string", "null"]}, "to": _VAR, "bind": _STRMAP,
              "llm": _arr(_LLM)}, ["name", "from", "to", "bind", "llm"])
_HANDOFF = _obj({"name": _STR, "sources": _arr(_STR), "target": _STR, "rules": _arr(_RULE)},
                ["name", "sources", "target", "rules"])
_GOAL = _obj({"class": _STR, "scope": _STR, "mode": {"type": "string", "enum": ["checked", "delivered"]},
              "anchors": _arr(_STR)}, ["class", "scope", "mode"])
_CLAUSE = {"type": "string", "enum": ["cover(G)", "valid", "fresh", "noEsc", *LIBRARY_CLAUSES]}

TEAM_SCHEMA: dict = _obj({
    "name": _STR, "agents": _arr(_AGENT), "views": {"type": "object", "additionalProperties": _VIEW},
    "writes": {"type": "object", "additionalProperties": _arr(_STR)}, "handoffs": _arr(_HANDOFF),
    "goal": _arr(_GOAL), "deliverable": _obj({"view": _STR, "class": _STR, "feature": _STR}, ["view", "class", "feature"]),
    "done": _arr(_CLAUSE),
}, ["name", "agents", "views", "writes", "handoffs", "goal", "deliverable", "done"])

FIX_SCHEMA: dict = {
    "type": "object", "required": ["fixes"],
    "properties": {"fixes": {"type": "array", "items": {
        "type": "object", "required": ["pointer", "value"],
        "properties": {"pointer": _STR, "value": {}}}}},
}


# --------------------------------------------------------------------------
# locating diagnostics in the JSON
# --------------------------------------------------------------------------

def _rule_pointers(team: dict) -> dict[str, str]:
    out = {}
    for hi, h in enumerate(team.get("handoffs") or []):
        if not isinstance(h, dict):
            continue
        for ri, r in enumerate(h.get("rules") or []):
            if isinstance(r, dict) and r.get("name"):
                out[str(r["name"])] = f"/handoffs/{hi}/rules/{ri}"
    return out


def _handoff_pointers(team: dict) -> dict[str, str]:
    return {str(h.get("name")): f"/handoffs/{i}" for i, h in enumerate(team.get("handoffs") or [])
            if isinstance(h, dict)}


def _class_pointer(team: dict, view: str, cls: str) -> str | None:
    v = (team.get("views") or {}).get(view)
    if isinstance(v, dict) and cls in (v.get("classes") or {}):
        return f"/views/{view}/classes/{cls}"
    return None


def get(team: dict, pointer: str) -> Any:
    cur: Any = team
    for part in pointer.strip("/").split("/"):
        if part == "":
            continue
        cur = cur[int(part)] if isinstance(cur, list) else cur[part]
    return cur


def put(team: dict, pointer: str, value: Any) -> None:
    parts = [p for p in pointer.strip("/").split("/") if p != ""]
    cur: Any = team
    for part in parts[:-1]:
        cur = cur[int(part)] if isinstance(cur, list) else cur.setdefault(part, {})
    last = parts[-1]
    if isinstance(cur, list):
        cur[int(last)] = value
    else:
        cur[last] = value


def fragments_for(team: dict, diagnostics: list) -> dict[str, Any]:
    """JSON pointer -> current fragment, for every element a diagnostic names
    (and the classes a diagnosed rule creates or reads)."""
    rules, hands = _rule_pointers(team), _handoff_pointers(team)
    ptrs: list[str] = []

    def add(p: str | None) -> None:
        if p and p not in ptrs:
            ptrs.append(p)

    def rule_and_classes(rname: str) -> None:
        p = rules.get(rname)
        add(p)
        if p:
            r = get(team, p)
            for t in [r.get("to", {}).get("type", "")] + [s.get("type", "") for s in r.get("from") or [] if isinstance(s, dict)]:
                if "!" in str(t):
                    v, c = str(t).split("!", 1)
                    add(_class_pointer(team, v, c))
            add(p.rsplit("/rules/", 1)[0] + "/sources")

    for d in diagnostics:
        el = str(getattr(d, "element", "") or "")
        cond = getattr(d, "cond", "")
        head = el.split(".")[0]
        if head in rules:
            rule_and_classes(head)
            if cond == "W6":
                add("/agents")
                add("/writes")
        elif el in hands:
            add(hands[el])
        elif cond == "W2" and el in (team.get("views") or {}):
            add("/writes")
        elif cond == "W2" and "." in el:
            v, c = el.split(".", 1)
            for rname, p in rules.items():
                r = get(team, p)
                if str(r.get("to", {}).get("type", "")) == f"{v}!{c}":
                    add(p)
        elif cond == "W4" and el in (team.get("views") or {}):
            add(f"/views/{el}")
            add("/handoffs")
        elif cond == "W4":
            # an unanchored or undelivered goal is fixed by changing the goal or the rules and
            # views that should carry it
            add("/goal")
            add("/deliverable")
            add("/handoffs")
            add("/views")
        elif cond == "W5":
            add("/done")
            if el == "views":
                add("/handoffs")
        elif el in (team.get("views") or {}):
            add(f"/views/{el}")
        else:
            add("/agents")
    # a pointer inside another one is covered by it
    keep = [p for p in ptrs if not any(q != p and p.startswith(q + "/") for q in ptrs)]
    return {p: copy.deepcopy(get(team, p)) for p in keep}


def path_catalogue(team: dict, rule_names: list[str], hops: int = 2) -> dict[str, list[str]]:
    """Navigation paths that type-check in each rule (from its source
    variables, at most `hops` references, ending in an attribute or object)."""
    try:
        tt = parse_team(normalize(team))
    except TeamFormatError:
        return {}
    out: dict[str, list[str]] = {}
    for _h, r in tt.rules():
        if r.name not in rule_names:
            continue
        env = rule_env(r)
        paths: list[str] = []
        frontier = [(v, tt.cls(view, c)) for v, view, c in r.sources]
        for depth in range(hops + 1):
            nxt = []
            for path, decl in frontier:
                if decl is None:
                    continue
                if depth:
                    paths.append(path)
                paths += [f"{path}.{a}" for a in decl.attrs]
                if depth < hops:
                    nxt += [(f"{path}.{rn}", tt.cls(ref.view, ref.cls)) for rn, ref in decl.refs.items()]
            frontier = nxt
        out[r.name] = [p for p in dict.fromkeys(paths) if type_path(tt, p, env).ok][:60]
    return out


def apply_fixes(team: dict, fixes: Any, allowed: set[str]) -> dict:
    """Put rewritten fragments back; only pointers that were offered."""
    new = copy.deepcopy(team)
    for f in fixes if isinstance(fixes, list) else []:
        if not isinstance(f, dict):
            continue
        p = str(f.get("pointer", ""))
        if p in allowed and "value" in f:
            try:
                put(new, p, f["value"])
            except (KeyError, IndexError, ValueError, TypeError):
                continue
    return new


def repair_prompt(team: dict, diagnostics: list, frags: dict[str, Any], catalogue: dict[str, list[str]],
                  rules_text: str, format_text: str, vlib_text: str) -> str:
    diag = "\n".join(d.with_hint() if hasattr(d, "with_hint") else str(d) for d in diagnostics)
    frag_text = "\n".join(f"{p}:\n{json.dumps(v, indent=1)}" for p, v in frags.items())
    cat = "\n".join(f"- rule {r}: {', '.join(ps)}" for r, ps in catalogue.items()) or "(none)"
    return f"""You are a team builder. The deterministic checker REJECTED your typed team. Fix it by rewriting ONLY the
fragments listed below. Everything else in the team stays exactly as it is.

{format_text}
Validator library:
{vlib_text}

{rules_text}
The whole team, for reference (do not repeat it):
{json.dumps(team, separators=(",", ":"))}

Checker diagnostics (fix every one):
{diag}

Fragments you may rewrite (JSON pointer, then its current value):
{frag_text}

Navigation paths that type-check in each diagnosed rule (use only these, or declare the features you need in the
views by rewriting the view's class):
{cat}

Answer JSON {{"fixes": [{{"pointer": <one of the pointers above>, "value": <the corrected fragment>}}]}}.
Rewrite a fragment completely (the full rule, the full class, the full "writes" map, ...)."""
