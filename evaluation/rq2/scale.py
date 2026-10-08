"""Checker cost on synthetic chain teams: n views, one hand-off between
consecutive views, one stochastic binding per rule (RQ2, Proposition 1).

    python -m evaluation.rq2.scale
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

from agentm2m.auto.checker import check
from agentm2m.auto.typed_team import parse_team


def chain(n: int) -> dict:
    views = {"V0": {"classes": {"C0": {"attributes": {"text": "string"}}}}}
    agents, writes, handoffs = [], {}, []
    for i in range(1, n + 1):
        views[f"V{i}"] = {"classes": {f"C{i}": {"attributes": {"name": "string", "text": "string"},
                                                 "references": {"src": {"type": f"V{i-1}.C{i-1}", "required": True}}}}}
        agents.append({"name": f"A{i}", "role": "", "tools": ["exec"]})
        writes[f"A{i}"] = [f"V{i}"]
        handoffs.append({"name": f"H{i}", "sources": [f"V{i-1}"], "target": f"V{i}", "rules": [{
            "name": f"R{i}", "from": [{"var": "x", "type": f"V{i-1}!C{i-1}"}],
            "to": {"var": "y", "type": f"V{i}!C{i}"}, "bind": {"name": "'n'", "src": "x"},
            "llm": [{"feature": "text", "prompt": "p", "footprint": ["x.text"], "validator": [{"id": "runs"}]}]}]})
    return {"name": f"chain{n}", "goal_view": "V0", "agents": agents, "views": views, "writes": writes,
            "handoffs": handoffs, "goal": [{"class": "C0"}], "done": ["cover(G)", "valid", "fresh", "noObl"]}


def main() -> int:
    rows = []
    for n in (10, 30, 100, 300, 1000, 3000):
        team = parse_team(chain(n))
        reps = 5 if n <= 300 else 1
        t0 = time.perf_counter()
        for _ in range(reps):
            res = check(team)
        dt = (time.perf_counter() - t0) / reps
        rows.append({"n_views": n, "seconds": dt, "admitted": res.admitted})
        print(f"n={n:5d}  {dt*1000:9.2f} ms  admitted={res.admitted}")
    out = Path("results/rq2/scale.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(rows, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
