"""Experiment 1 analysis (hpc/docs/preregistration-exp1-budget-scaling.md).

    python hpc/scripts/exp1_analyze.py --sweep <RUN_DIR of the sweep> --study <results dir of the study run> \
        --tasks dev|heldout --out <dir>

Reads <sweep>/results/k<K>/raw/<bench>__<model>.jsonl (AutoM2M and Single-Gate at k = K) and, for fixed reference
lines, Single / Free / Critic from the study run on the same tasks and seeds. Writes:
  exp1_curves.csv   bench, model, condition, k, size (all|small|large), success_pct, runs, mean_out_tokens
  exp1_tests.csv    the pre-registered tests (H1, H2, H3, plateau) with estimate, CI or p
Needs the harness venv (benchmark loader for class sizes, statsmodels for GEE).
"""
from __future__ import annotations

import argparse
import csv
import glob
import json
import math
import random
from pathlib import Path

KS = (1, 2, 4, 8)


def load_rows(path: Path) -> dict:
    out = {}
    if path.exists():
        for line in path.read_text().splitlines():
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            out[(r["task"], r["condition"], r["seed"])] = r          # last row of a run is its record
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sweep", required=True)
    ap.add_argument("--study", required=True)
    ap.add_argument("--tasks", default="dev")
    ap.add_argument("--hpc", default=str(Path(__file__).resolve().parents[1]))
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    from evaluation.benchmarks import tasks as T

    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    curves, tests, obs = [], [], []
    for bench in ("classeval", "humanevalplus"):
        tf = Path(a.hpc) / "config" / f"{a.tasks}_tasks_{bench}.txt"
        if not tf.exists():
            continue
        task_ids = [l.strip() for l in tf.read_text().splitlines() if l.strip()]
        nmeth = {t.task_id: len(t.methods) for t in T.load(bench)}
        size = {t: ("small" if nmeth[t] <= 3 else "large" if nmeth[t] >= 6 else "mid") for t in task_ids}
        models = sorted({Path(f).name.split("__")[1][:-6] for f in glob.glob(f"{a.sweep}/results/k*/raw/{bench}__*.jsonl")})
        for msafe in models:
            study = load_rows(Path(a.study) / "raw" / f"{bench}__{msafe}.jsonl")
            per_k = {k: load_rows(Path(a.sweep) / "results" / f"k{k}" / "raw" / f"{bench}__{msafe}.jsonl") for k in KS}
            seeds = sorted({s for k in KS for (_t, _c, s) in per_k[k]})
            for k in KS:
                for cond in ("autom2m", "single_gate"):
                    rs = [per_k[k].get((t, cond, s)) for t in task_ids for s in seeds]
                    rs = [r for r in rs if r and r.get("status") != "error"]
                    for r in rs:
                        obs.append({"bench": bench, "model": msafe, "condition": cond, "k": k, "task": r["task"],
                                    "seed": r["seed"], "success": int(bool(r["success"])), "size": size[r["task"]]})
                    for sz in ("all", "small", "large"):
                        sub = [r for r in rs if sz == "all" or size[r["task"]] == sz]
                        if not sub:
                            continue
                        toks = [(r.get("total") or {}).get("out_tokens", 0) for r in sub]
                        curves.append({"bench": bench, "model": msafe, "condition": cond, "k": k, "size": sz,
                                       "success_pct": round(100 * sum(bool(r["success"]) for r in sub) / len(sub), 1),
                                       "runs": len(sub), "mean_out_tokens": round(sum(toks) / len(toks))})
            for cond in ("single", "free", "critic"):                 # fixed reference lines, same tasks and seeds
                rs = [study.get((t, cond, s)) for t in task_ids for s in seeds]
                rs = [r for r in rs if r and r.get("status") != "error"]
                for sz in ("all", "small", "large"):
                    sub = [r for r in rs if sz == "all" or size[r["task"]] == sz]
                    if not sub:
                        continue
                    toks = [(r.get("total") or {}).get("out_tokens", 0) for r in sub]
                    for k in KS:
                        curves.append({"bench": bench, "model": msafe, "condition": cond, "k": k, "size": sz,
                                       "success_pct": round(100 * sum(bool(r["success"]) for r in sub) / len(sub), 1),
                                       "runs": len(sub), "mean_out_tokens": round(sum(toks) / len(toks))})
    with (out / "exp1_curves.csv").open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=["bench", "model", "condition", "k", "size", "success_pct", "runs",
                                           "mean_out_tokens"])
        w.writeheader()
        w.writerows(curves)

    # ---- pre-registered tests ------------------------------------------------------------------------------------
    import pandas as pd
    import statsmodels.api as sm
    import statsmodels.formula.api as smf

    df = pd.DataFrame(obs)

    def gee(sub: pd.DataFrame, formula: str, term: str):
        try:
            m = smf.gee(formula, groups="task", data=sub, family=sm.families.Binomial(),
                        cov_struct=sm.cov_struct.Exchangeable()).fit()
            return float(m.params[term]), float(m.pvalues[term]), float(m.conf_int().loc[term, 0]), float(m.conf_int().loc[term, 1])
        except Exception as exc:  # noqa: BLE001 - too little variation (e.g. a pilot cell): reported, not hidden
            return None, None, None, str(exc)[:120]

    if not df.empty:
        df["log2k"] = df.k.map(lambda k: math.log2(k))
        df["am"] = (df.condition == "autom2m").astype(int)
        for (bench, model), sub in df.groupby(["bench", "model"]):
            est, p, lo, hi = gee(sub, "success ~ am * log2k", "am:log2k")
            tests.append({"bench": bench, "model": model, "test": "H1/H3 condition x log2(k)" if bench == "classeval" else
                          "H3 (negative control) condition x log2(k)", "estimate": est, "p": p, "ci_low": lo, "ci_high": hi})
            if bench == "classeval":
                s2 = sub[sub["size"].isin(["small", "large"])].copy()
                s2["large"] = (s2["size"] == "large").astype(int)
                est, p, lo, hi = gee(s2, "success ~ am * log2k * large", "am:log2k:large")
                tests.append({"bench": bench, "model": model, "test": "H2 condition x log2(k) x large", "estimate": est,
                              "p": p, "ci_low": lo, "ci_high": hi})
            for cond in ("autom2m", "single_gate"):                    # plateau: success(k=8) - success(k=4)
                c = sub[sub.condition == cond]
                by_task = {t: g for t, g in c.groupby("task")}
                tasks_ = list(by_task)

                def diff(ts):
                    hi_ = [x for t in ts for x in by_task[t][by_task[t].k == 8].success]
                    lo_ = [x for t in ts for x in by_task[t][by_task[t].k == 4].success]
                    return (sum(hi_) / len(hi_) - sum(lo_) / len(lo_)) * 100 if hi_ and lo_ else float("nan")
                rng = random.Random(0)
                boots = sorted(diff([rng.choice(tasks_) for _ in tasks_]) for _ in range(2000)) if tasks_ else []
                boots = [b for b in boots if b == b]
                tests.append({"bench": bench, "model": model, "test": f"plateau {cond}: success(k=8) - success(k=4), points",
                              "estimate": diff(tasks_) if tasks_ else None, "p": None,
                              "ci_low": boots[int(.025 * len(boots))] if boots else None,
                              "ci_high": boots[int(.975 * len(boots)) - 1] if boots else None})
    with (out / "exp1_tests.csv").open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=["bench", "model", "test", "estimate", "p", "ci_low", "ci_high"])
        w.writeheader()
        w.writerows(tests)
    print(f"curves: {len(curves)} rows, tests: {len(tests)} -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
