"""Mutation study of the W1–W6 checker (RQ2, static).

Nine operators, one or two per defect class, applied at every applicable
site of each admitted team. Every mutant is checked with anchored and naive
W4. Output: one JSON line per mutant and a summary table.

    python -m evaluation.rq2.mutate teams/devteam_admitted.json teams/chakin_repaired.json
"""
from __future__ import annotations

import argparse
import copy
import json
import sys
from collections import defaultdict
from pathlib import Path

from agentm2m.auto.checker import check
from agentm2m.auto.typed_team import DONE_VOCABULARY, parse_team, rule_env, type_path

OPERATORS = {
    "delete_rule": ("D1", "W4"),
    "detach_goal": ("D1", "W4"),
    "break_footprint": ("D2", "W1"),
    "drop_mandatory": ("D2", "W3"),
    "second_writer": ("D3", "W2"),
    "duplicate_rule": ("D3", "W2"),
    "add_claim": ("D4", "W5"),
    "drop_clause": ("D4", "W5"),
    "remove_tool": ("D5", "W6"),
}


def _rules(t: dict):
    for hi, h in enumerate(t["handoffs"]):
        for ri, r in enumerate(h["rules"]):
            yield hi, ri, h, r


def mutants(team: dict):
    """Yield (operator, site, mutant_json)."""
    tt = parse_team(team)
    gv = tt.goal_view
    # D1: delete a rule
    for hi, ri, h, r in _rules(team):
        m = copy.deepcopy(team)
        del m["handoffs"][hi]["rules"][ri]
        if not m["handoffs"][hi]["rules"]:
            del m["handoffs"][hi]
        yield "delete_rule", r["name"], m
    # D1: detach goal data from all footprints (rules kept)
    m = copy.deepcopy(team)
    changed = False
    parsed_rules = {r.name: r for _h, r in tt.rules()}
    for _hi, _ri, _h, r in _rules(m):
        env = rule_env(parsed_rules[r["name"]])
        for b in r.get("llm", []):
            keep = [p for p in b["footprint"] if gv not in type_path(tt, p, env).views]
            if len(keep) != len(b["footprint"]):
                changed = True
                b["footprint"] = keep or ["'(no context)'"]
    if changed:
        yield "detach_goal", "all footprints", m
    # D2: break a footprint path
    for hi, ri, _h, r in _rules(team):
        for bi, b in enumerate(r.get("llm", [])):
            for pi, p in enumerate(b["footprint"]):
                m = copy.deepcopy(team)
                m["handoffs"][hi]["rules"][ri]["llm"][bi]["footprint"][pi] = p + "Ref"
                yield "break_footprint", f"{r['name']}.{b['feature']}[{p}]", m
    # D2: drop a mandatory binding
    for hi, ri, _h, r in _rules(team):
        tv, tc = r["to"]["type"].split("!")
        decl = tt.cls(tv, tc)
        for f in list(r.get("bind", {})):
            feat = decl.feature(f) if decl else None
            if feat is not None and feat.required:
                m = copy.deepcopy(team)
                del m["handoffs"][hi]["rules"][ri]["bind"][f]
                yield "drop_mandatory", f"{r['name']}.{f}", m
        for bi, b in enumerate(r.get("llm", [])):
            feat = decl.feature(b["feature"]) if decl else None
            if feat is not None and feat.required:
                m = copy.deepcopy(team)
                del m["handoffs"][hi]["rules"][ri]["llm"][bi]
                yield "drop_mandatory", f"{r['name']}.{b['feature']}", m
    # D3: add a second writer to a view
    agents = [a["name"] for a in team["agents"]]
    for v in team["views"]:
        if v == gv:
            continue
        for a in agents:
            if v not in team["writes"].get(a, []):
                m = copy.deepcopy(team)
                m["writes"].setdefault(a, []).append(v)
                yield "second_writer", f"{a}+{v}", m
    # D3: duplicate a producing rule
    for hi, ri, _h, r in _rules(team):
        m = copy.deepcopy(team)
        dup = copy.deepcopy(r)
        dup["name"] = r["name"] + "Copy"
        m["handoffs"][hi]["rules"].append(dup)
        yield "duplicate_rule", r["name"], m
    # D4: add an agent's claim to phi / drop an engine clause
    for a in agents:
        m = copy.deepcopy(team)
        m["done"].append(f"{a}.says('DONE')")
        yield "add_claim", a, m
    for c in DONE_VOCABULARY:
        m = copy.deepcopy(team)
        m["done"] = [d for d in m["done"] if d != c]
        yield "drop_clause", c, m
    # D5: remove a required tool
    needed = set()
    for _h, r in tt.rules():
        for b in r.llm:
            from agentm2m.auto.vlib import VLIB

            for v in b.validators:
                needed |= set(VLIB[v.id].tools) if v.id in VLIB else set()
    for ai, a in enumerate(team["agents"]):
        owns_needing = False
        for h in tt.handoffs:
            if h.target in team["writes"].get(a["name"], []):
                owns_needing = owns_needing or any(VLIB[v.id].tools for r in h.rules for b in r.llm for v in b.validators if v.id in VLIB)
        for tool in a.get("tools", []):
            if tool in needed and owns_needing:
                m = copy.deepcopy(team)
                m["agents"][ai]["tools"] = [x for x in a["tools"] if x != tool]
                yield "remove_tool", f"{a['name']}-{tool}", m


def run(paths: list[str], out: Path | None = None, label: str = "") -> list[dict]:
    rows = []
    for p in paths:
        team = json.loads(Path(p).read_text())
        base_a, base_n = check(team), check(team, anchored=False)
        rows.append({"team": Path(p).stem, "operator": "none", "site": "-", "defect": "-",
                     "anchored": sorted(base_a.conds()), "naive": sorted(base_n.conds()),
                     "anchored_detected": not base_a.admitted, "naive_detected": not base_n.admitted, "targeted_hit": None})
        for op, site, m in mutants(team):
            ra, rn = check(m), check(m, anchored=False)
            rows.append({
                "team": Path(p).stem, "operator": op, "site": site, "defect": OPERATORS[op][0],
                "anchored": sorted(ra.conds()), "naive": sorted(rn.conds()),
                "anchored_detected": not ra.admitted, "naive_detected": not rn.admitted,
                "targeted_hit": OPERATORS[op][1] in ra.conds(),
                "anchored_msgs": [str(d) for d in ra.diagnostics],
            })
    if out:
        out.parent.mkdir(parents=True, exist_ok=True)
        with out.open("w") as fh:
            for r in rows:
                fh.write(json.dumps({**r, "config": label}) + "\n")
    return rows


def summarize(rows: list[dict]) -> dict:
    agg = defaultdict(lambda: {"n": 0, "anchored": 0, "naive": 0, "targeted": 0})
    for r in rows:
        if r["operator"] == "none":
            continue
        a = agg[r["operator"]]
        a["n"] += 1
        a["anchored"] += r["anchored_detected"]
        a["naive"] += r["naive_detected"]
        a["targeted"] += bool(r["targeted_hit"])
    return dict(agg)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("teams", nargs="+")
    ap.add_argument("--out", type=Path)
    ap.add_argument("--label", default="")
    a = ap.parse_args(argv)
    rows = run(a.teams, a.out, a.label)
    s = summarize(rows)
    tot = {"n": 0, "anchored": 0, "naive": 0, "targeted": 0}
    print(f"{'operator':18} {'defect':6} {'n':>4} {'anch':>5} {'naive':>5} {'target':>6}")
    for op, (d, _w) in OPERATORS.items():
        x = s.get(op, {"n": 0, "anchored": 0, "naive": 0, "targeted": 0})
        print(f"{op:18} {d:6} {x['n']:4} {x['anchored']:5} {x['naive']:5} {x['targeted']:6}")
        for k in tot:
            tot[k] += x[k]
    print(f"{'total':18} {'':6} {tot['n']:4} {tot['anchored']:5} {tot['naive']:5} {tot['targeted']:6}")
    fa = [r for r in rows if r["operator"] == "none" and r["anchored_detected"]]
    print("false alarms on unmutated teams:", len(fa))
    missed = [r for r in rows if r["operator"] != "none" and not r["anchored_detected"]]
    for r in missed:
        print("  MISSED (anchored):", r["team"], r["operator"], r["site"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
