"""RQ2 mutation analysis of the W1-W6 checker (paper Table 7).

Nine operators, one or two per defect class, applied at every applicable
site of the two hand-written admitted teams (the typed DevTeam and the
requirements team). Every mutant is checked with three W4 variants
(path-only, one-sided, two-sided) under two goal configurations: G1 declares
only checked obligations, G2 also the deliverables.

    python -m evaluation.rq2.mutate                      # both teams, writes results/rq2/mutants.jsonl
    python -m evaluation.rq2.mutate teams/devteam_admitted.json
"""
from __future__ import annotations

import argparse
import copy
import json
import sys
from collections import defaultdict
from pathlib import Path

from autom2m.checker import W4_MODES, check
from autom2m.lift import normalize
from autom2m.typed_team import DONE_VOCABULARY, parse_team, rule_env, type_path
from autom2m.vlib import VLIB

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_TEAMS = [ROOT / "teams" / "devteam_admitted.json", ROOT / "teams" / "reqteam_admitted.json"]
G2_EXTRA = {"devteam": {"class": "Method", "scope": "all", "mode": "delivered"},
            "reqteam": {"class": "UserStory", "scope": "s.status = 'accepted'", "mode": "delivered"}}

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


def _prep(team: dict) -> dict:
    return normalize(team) if team.get("goal_view") == "Goal" else team


def with_goal(team: dict, config: str) -> dict:
    """G1: the team as given (checked obligations only); G2: + deliverables."""
    t = copy.deepcopy(team)
    if config == "G2":
        extra = G2_EXTRA.get(str(t.get("name")))
        if extra and extra not in t.get("goal", []):
            t["goal"] = list(t.get("goal", [])) + [extra]
    return t


def verdicts(team: dict) -> dict:
    out = {}
    for config in ("G1", "G2"):
        for mode in W4_MODES:
            r = check(_prep(with_goal(team, config)), w4=mode)
            out[f"{mode}|{config}"] = {"rejected": not r.admitted, "conds": sorted(r.conds())}
    return out


def run(paths: list, out: Path | None = None) -> list[dict]:
    rows = []
    for p in paths:
        team = json.loads(Path(p).read_text())
        name = Path(p).stem
        rows.append({"team": name, "operator": "none", "site": "-", "defect": "-", "checks": verdicts(team)})
        for op, site, m in mutants(_prep(team)):
            rows.append({"team": name, "operator": op, "site": site, "defect": OPERATORS[op][0], "checks": verdicts(m)})
    if out:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text("".join(json.dumps(r) + "\n" for r in rows))
    return rows


def summarize(rows: list[dict]) -> dict:
    agg: dict = defaultdict(lambda: defaultdict(int))
    for r in rows:
        if r["operator"] == "none":
            continue
        a = agg[r["operator"]]
        a["n"] += 1
        for k, v in r["checks"].items():
            a[k] += v["rejected"]
    return {k: dict(v) for k, v in agg.items()}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("teams", nargs="*")
    ap.add_argument("--out", type=Path, default=ROOT / "results" / "rq2" / "mutants.jsonl")
    a = ap.parse_args(argv)
    rows = run(a.teams or DEFAULT_TEAMS, a.out)
    s = summarize(rows)
    cols = [f"{m}|{g}" for m in W4_MODES for g in ("G1", "G2")]
    print(f"{'operator':16} {'D':3} {'n':>3} " + " ".join(f"{c:>15}" for c in cols))
    tot = defaultdict(int)
    for op, (d, _w) in OPERATORS.items():
        x = s.get(op, {})
        print(f"{op:16} {d:3} {x.get('n', 0):3} " + " ".join(f"{x.get(c, 0):15}" for c in cols))
        for c in ["n"] + cols:
            tot[c] += x.get(c, 0)
    print(f"{'total':16} {'':3} {tot['n']:3} " + " ".join(f"{tot[c]:15}" for c in cols))
    fa = [r for r in rows if r["operator"] == "none" and any(v["rejected"] for v in r["checks"].values())]
    print("unmutated teams rejected:", [(r["team"], {k: v["conds"] for k, v in r["checks"].items() if v["rejected"]}) for r in fa])
    for r in rows:
        if r["operator"] != "none" and not r["checks"]["two-sided|G2"]["rejected"]:
            print("  MISSED (two-sided, G2):", r["team"], r["operator"], r["site"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
