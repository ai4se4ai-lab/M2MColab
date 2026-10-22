"""RQ2: the mutation operators applied to builder-generated (natural)
admitted typed teams, so that detection is not measured only on the authors'
teams. In particular the "detach" operator (remove all goal data from all
footprints, keep every rule), checked with one- and two-sided W4.

    python -m evaluation.rq2.natural_mutants
"""
from __future__ import annotations

import json
import os
from collections import defaultdict
from pathlib import Path

from autom2m.checker import W4_MODES, check
from autom2m.lift import normalize
from evaluation.rq2.independent import builder_teams
from evaluation.rq2.mutate import OPERATORS, mutants

ROOT = Path(__file__).resolve().parents[2]
RESULTS = Path(os.environ.get("AM2M_RESULTS", ROOT / "results"))


def main() -> int:
    teams = builder_teams()
    agg = defaultdict(lambda: defaultdict(int))
    readmission_failures = 0
    mutation_failures: list[dict] = []
    for item in teams:
        t = normalize(item["team"])
        if not check(t).admitted:
            readmission_failures += 1  # should not happen: these teams were admitted
            continue
        # an admitted team whose JSON shape the mutation operators cannot handle is skipped and counted (with the
        # error), never silently dropped; its partial mutants are discarded so every team counts fully or not at all
        local = defaultdict(lambda: defaultdict(int))
        try:
            for op, _site, m in mutants(t):
                a = local[op]
                a["n"] += 1
                for mode in W4_MODES:
                    a[mode] += not check(m, w4=mode).admitted
                a["targeted"] += OPERATORS[op][1] in check(m).conds()
        except Exception as exc:  # noqa: BLE001
            mutation_failures.append({"source": item.get("source"), "error": f"{type(exc).__name__}: {exc}"[:300]})
            continue
        for op, v in local.items():
            for k, n in v.items():
                agg[op][k] += n
    out = {"teams": len(teams), "readmission_failures": readmission_failures,
           "mutation_failures": len(mutation_failures), "mutation_failure_detail": mutation_failures,
           "operators": {op: dict(v) for op, v in agg.items()}}
    p = RESULTS / "rq2" / "natural_mutants.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(out, indent=1))
    print(json.dumps(out, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
