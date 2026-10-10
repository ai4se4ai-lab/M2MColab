"""RQ2, cost of checking (Fig. 7a, 7b): checker time on synthetic teams.

Chain teams: view i reads view i-1. DAG teams: each view reads three earlier
views (fan-in three). One stochastic binding with a behaviour validator per
rule; the goal anchors at the first view. 10 runs per size; per-condition
times (W1..W6) at every size. Checks per run and the wall-clock of a run by
step (Fig. 7c, 7d) come from the run logs (analysis).

    python -m evaluation.rq2.scale
"""
from __future__ import annotations

import json
import statistics
import sys
import time
from pathlib import Path

from autom2m.checker import Checker
from autom2m.typed_team import parse_team

ROOT = Path(__file__).resolve().parents[2]
SIZES = (10, 30, 100, 300, 1000, 2000, 3000)
DONE = ["cover(G)", "valid", "fresh", "noEsc"]


def synthetic(n: int, fan_in: int = 1) -> dict:
    views = {"V0": {"classes": {"C0": {"attributes": {"name": "string", "text": "string"}}}}}
    agents, writes, handoffs = [], {}, []
    for i in range(1, n + 1):
        srcs = sorted({max(0, i - k) for k in range(1, fan_in + 1)})
        refs = {f"s{j}": {"type": f"V{j}.C{j}", "required": True} for j in srcs}
        views[f"V{i}"] = {"classes": {f"C{i}": {"attributes": {"name": "string", "text": "string"}, "references": refs}}}
        agents.append({"name": f"A{i}", "role": "", "tools": ["exec"]})
        writes[f"A{i}"] = [f"V{i}"]
        frm = [{"var": f"x{j}", "type": f"V{j}!C{j}"} for j in srcs]
        guard = " and ".join(f"x{srcs[0]}.name = x{j}.name" for j in srcs[1:]) or None
        if srcs == [0]:
            guard = None
        bind = {"name": "'n'", **{f"s{j}": f"x{j}" for j in srcs}}
        fp = [f"x{j}.text" for j in srcs]
        handoffs.append({"name": f"H{i}", "sources": [f"V{j}" for j in srcs], "target": f"V{i}", "rules": [{
            "name": f"R{i}", "from": frm, "guard": guard, "to": {"var": "y", "type": f"V{i}!C{i}"}, "bind": bind,
            "llm": [{"feature": "text", "prompt": "p", "footprint": fp,
                     "validator": [{"id": "passes_tests", "args": {"tests": fp[0]}}]}]}]})
    return {"name": f"syn{n}_{fan_in}", "goal_view": "V0", "agents": agents, "views": views, "writes": writes,
            "handoffs": handoffs, "goal": [{"class": "C0", "mode": "checked", "anchors": ["text"]}], "done": list(DONE)}


def main(argv=None) -> int:
    reps = 10
    rows = []
    for topo, fan in (("chain", 1), ("dag", 3)):
        for n in SIZES:
            team = parse_team(synthetic(n, fan))
            times, conds = [], []
            admitted = None
            for _ in range(reps):
                t0 = time.perf_counter()
                res = Checker(team).run()
                times.append(time.perf_counter() - t0)
                conds.append(res.cond_seconds)
                admitted = res.admitted
            row = {"topology": topo, "n_views": n, "median_s": statistics.median(times), "min_s": min(times),
                   "max_s": max(times), "admitted": admitted,
                   "cond_median_s": {c: statistics.median(x[c] for x in conds) for c in conds[0]}}
            rows.append(row)
            print(f"{topo:5} n={n:5d} median {row['median_s']*1000:9.2f} ms admitted={admitted} "
                  + " ".join(f"{c}={v*1000:.1f}" for c, v in row["cond_median_s"].items()), flush=True)
    out = ROOT / "results" / "rq2" / "scale.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(rows, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
