"""RQ2: the mutation operators applied to builder-generated (natural)
admitted typed teams, so that detection is not measured only on the authors'
teams. In particular the "detach" operator (remove all goal data from all
footprints, keep every rule), checked with one- and two-sided W4.

    python -m evaluation.rq2.natural_mutants
"""
from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

from autom2m.checker import W4_MODES, check
from autom2m.lift import normalize
from evaluation.rq2.independent import builder_teams
from evaluation.rq2.mutate import OPERATORS, mutants

ROOT = Path(__file__).resolve().parents[2]


def main() -> int:
    teams = builder_teams()
    agg = defaultdict(lambda: defaultdict(int))
    readmission_failures = 0
    for item in teams:
        t = normalize(item["team"])
        if not check(t).admitted:
            readmission_failures += 1  # should not happen: these teams were admitted
            continue
        for op, _site, m in mutants(t):
            a = agg[op]
            a["n"] += 1
            for mode in W4_MODES:
                a[mode] += not check(m, w4=mode).admitted
            a["targeted"] += OPERATORS[op][1] in check(m).conds()
    out = {"teams": len(teams), "readmission_failures": readmission_failures,
           "operators": {op: dict(v) for op, v in agg.items()}}
    p = ROOT / "results" / "rq2" / "natural_mutants.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(out, indent=1))
    print(json.dumps(out, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
