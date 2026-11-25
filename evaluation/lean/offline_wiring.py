"""Offline study of wiring inference on recorded builder proposals (no LLM call).

For every recorded proposal (AutoM2M first proposals and Typed-NC proposals
of a raw-results directory), check it as proposed and after wiring
inference, and report admission rates and the remaining diagnostics.

    python -m evaluation.lean.offline_wiring --runs results/runs --model qwen2.5-coder_7b
"""
from __future__ import annotations

import argparse
import collections
import glob
import json
import re
from pathlib import Path

from autom2m.checker import check
from autom2m.lift import normalize
from autom2m.typed_team import TeamFormatError, parse_team
from autom2m.wiring import infer


def admit(team) -> tuple[bool, list[str]]:
    try:
        res = check(parse_team(normalize(team)))
    except TeamFormatError as exc:
        return False, [f"W1 {exc}"]
    return res.admitted, [str(d) for d in res.diagnostics]


def proposals(runs: Path, model: str):
    for bench in ("classeval", "humanevalplus"):
        for cond, key in (("autom2m", "first_team"), ("typed_nc", "first_team")):
            for f in sorted(glob.glob(str(runs / bench / model / cond / "*.json"))):
                d = json.loads(Path(f).read_text())
                team = (d.get("detail") or {}).get(key)
                if isinstance(team, dict):
                    yield bench, cond, Path(f).stem, team


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", default="results/runs")
    ap.add_argument("--model", default="qwen2.5-coder_7b")
    ap.add_argument("--out", default="")
    ap.add_argument("--show", type=int, default=25)
    a = ap.parse_args(argv)
    stats = collections.defaultdict(lambda: collections.Counter())
    left = collections.Counter()
    rows = []
    for bench, cond, run, team in proposals(Path(a.runs), a.model):
        ok0, d0 = admit(team)
        try:
            kind = "class" if bench == "classeval" else "function"
            t1 = infer(team, kind)
            ok1, d1 = admit(t1)
        except TeamFormatError as exc:
            ok1, d1 = False, [f"W1 {exc}"]
        except Exception as exc:  # noqa: BLE001 - an inference crash is a bug to report
            ok1, d1 = False, [f"CRASH {type(exc).__name__}: {exc}"]
        s = stats[(bench, cond)]
        s["n"] += 1
        s["admitted_as_proposed"] += ok0
        s["admitted_after_inference"] += ok1
        for d in d1:
            left[re.sub(r"'[^']*'|`[^`]*`", "X", d.split("\n")[0])[:120]] += 1
        rows.append({"bench": bench, "condition": cond, "run": run, "admitted_as_proposed": ok0,
                     "admitted_after_inference": ok1, "diagnostics_before": d0, "diagnostics_after": d1})
    for k, s in sorted(stats.items()):
        print(f"{k[0]:14s} {k[1]:9s} n={s['n']:4d}  as proposed {s['admitted_as_proposed']:4d} "
              f"({100*s['admitted_as_proposed']/s['n']:.0f}%)  after inference {s['admitted_after_inference']:4d} "
              f"({100*s['admitted_after_inference']/s['n']:.0f}%)")
    print("remaining diagnostics:")
    for m, c in left.most_common(a.show):
        print(f"  {c:4d}  {m}")
    if a.out:
        Path(a.out).parent.mkdir(parents=True, exist_ok=True)
        Path(a.out).write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
