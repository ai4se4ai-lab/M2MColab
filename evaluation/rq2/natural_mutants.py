"""RQ2 static: the mutation operators applied to builder-generated (natural)
admitted typed teams from the AutoM2M runs, so that detection is not measured
only on the authors' own teams. Also summarises which conditions the
builders' first proposals violated.

    python -m evaluation.rq2.natural_mutants
"""
from __future__ import annotations

import glob
import json
from collections import Counter, defaultdict
from pathlib import Path

from agentm2m.auto.checker import check
from agentm2m.auto.repair import normalize
from evaluation.rq2.mutate import OPERATORS, mutants

ROOT = Path(__file__).resolve().parents[2]


def main() -> int:
    agg = defaultdict(lambda: defaultdict(lambda: [0, 0, 0, 0]))  # model -> op -> [n, anchored, naive, targeted]
    teams = defaultdict(int)
    false_alarm = defaultdict(int)
    first = defaultdict(Counter)
    first_n = defaultdict(int)
    seen = set()
    for f in sorted(glob.glob(str(ROOT / "results" / "runs" / "*" / "*" / "autom2m" / "*.json"))):
        d = json.loads(Path(f).read_text())
        model = d["model"]
        det = d.get("detail") or {}
        diags = det.get("diagnostics") or []
        if diags:
            first_n[model] += 1
            for x in set(s[:2] for s in diags[0]):
                first[model][x] += 1
        if d.get("status") not in ("done", "failed"):
            continue
        t = det.get("final_team")
        if not t:
            continue
        t = normalize(t)
        key = json.dumps(t, sort_keys=True)
        if key in seen:
            continue
        seen.add(key)
        if not check(t).admitted:
            false_alarm[model] += 1  # should not happen: these teams were admitted
            continue
        teams[model] += 1
        for op, _site, m in mutants(t):
            ra, rn = check(m), check(m, anchored=False)
            a = agg[model][op]
            a[0] += 1
            a[1] += not ra.admitted
            a[2] += not rn.admitted
            a[3] += OPERATORS[op][1] in ra.conds()
    out = {"teams": dict(teams), "readmission_failures": dict(false_alarm),
           "operators": {m: {op: v for op, v in ops.items()} for m, ops in agg.items()},
           "first_proposals": {m: {"n": first_n[m], "violations": dict(first[m])} for m in first_n}}
    p = ROOT / "results" / "rq2" / "natural_mutants.json"
    p.write_text(json.dumps(out, indent=1))
    print(json.dumps(out, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
