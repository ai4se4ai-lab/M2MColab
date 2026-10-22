"""Deterministic completion of a team's deliverable specification (part of Build).

The rule that writes the deliverable (one Python source per goal Method) is where the solution is produced. A
single agent reads the method's full specification (signature, docstring and its documented examples); a typed team
showed its coder only what the builder put into that rule's footprint, and builder-generated teams, like the worked
examples they imitate, often left out the docstring and the examples. This completion gives the deliverable binding
the goal method's specification and, when its writing agent can execute code, checks the produced code against the
method's documented examples with the library validator `examples_run`.

It is applied after the checker's verdict, to the team that is compiled and executed (loop.AutoM2M._admit): the
checker judges every team exactly as the builder wrote it (admission statistics, the RQ2 studies), and the
completion only adds paths that type-check as the goal Method's own features and a validator its writer can run, so
an admitted team stays admissible.
"""
from __future__ import annotations

import copy
from typing import Any

SPEC_SUFFIXES = ("docstring", "examples.call", "examples.expected")


def complete_deliverable_spec(team: dict) -> dict:
    """Return the team with the deliverable binding's footprint and validators completed (a copy; `team` itself is
    returned unchanged when nothing applies or the team cannot be parsed)."""
    from .typed_team import parse_team, rule_env, type_path

    try:
        tt = parse_team(team)
    except Exception:  # noqa: BLE001 - an unparsable proposal is left to the checker's diagnostics
        return team
    d = tt.deliverable or {}
    view, cls, feat = d.get("view"), d.get("class"), d.get("feature")
    if not (view and cls and feat):
        return team
    goal_method = f"{tt.goal_view}.Method"
    parsed = {r.name: r for _h, r in tt.rules()}
    owners = tt.owner_of(str(view))
    can_exec = bool(owners) and all("exec" in tt.agents[a].tools for a in owners if a in tt.agents)
    out = copy.deepcopy(team)
    changed = False
    for h in out.get("handoffs") or []:
        if not isinstance(h, dict):
            continue
        for r in h.get("rules") or []:
            if not isinstance(r, dict):
                continue
            pr = parsed.get(r.get("name"))
            if pr is None or (pr.target_view, pr.target_cls) != (view, cls):
                continue
            mp = (pr.bind or {}).get("method")
            if not isinstance(mp, str):
                continue
            pt = type_path(tt, mp, rule_env(pr), object_reads_all=False)
            if not (pt.ok and pt.end_kind == "object" and pt.end_type == goal_method and not pt.many):
                continue
            for b in r.get("llm") or []:
                if not isinstance(b, dict) or b.get("feature") != feat:
                    continue
                fp = b.get("footprint")
                fp = [fp] if isinstance(fp, str) else list(fp or [])
                for suffix in SPEC_SUFFIXES:
                    p = f"{mp}.{suffix}"
                    if p not in fp:
                        fp.append(p)
                        changed = True
                b["footprint"] = fp
                key = "validators" if "validators" in b and "validator" not in b else "validator"
                vs: Any = b.get(key)
                vs = [vs] if isinstance(vs, dict) else list(vs or [])
                if can_exec and not any(isinstance(v, dict) and v.get("id") == "examples_run" for v in vs):
                    # the documented examples are the specification's own checks: run them before the team's tests
                    at = next((i for i, v in enumerate(vs) if isinstance(v, dict) and v.get("id") == "passes_tests"),
                              len(vs))
                    vs.insert(at, {"id": "examples_run", "args": {"method": mp}})
                    changed = True
                b[key] = vs
    return out if changed else team
