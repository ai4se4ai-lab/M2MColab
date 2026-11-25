"""Cost and effect of AutoM2M-Lean against the other conditions (raw rows).

    python -m evaluation.lean.compare results/smoke/raw/*.jsonl
    python -m evaluation.lean.compare hpc/processed/full/raw/*.jsonl --paired autom2m,autom2m_base

Per benchmark and condition: runs, success, admitted share, precision of the
condition's own "done", median and mean total / output tokens, tokens by
role, LLM calls; with --paired, the per-task ratio of two conditions' tokens
on the tasks both completed.
"""
from __future__ import annotations

import argparse
import collections
import json
import statistics as st
from pathlib import Path


def load(paths: list[str]) -> list[dict]:
    rows = []
    for p in paths:
        for line in Path(p).read_text().splitlines():
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            if r.get("status") != "error":
                rows.append(r)
    return rows


def tok(r: dict, kind: str = "all") -> int:
    t = r.get("total") or {}
    return (t.get("in_tokens", 0) + t.get("out_tokens", 0)) if kind == "all" else t.get(f"{kind}_tokens", 0)


def summarize(rows: list[dict]) -> list[dict]:
    by = collections.defaultdict(list)
    for r in rows:
        by[(r["bench"], r["model"], r["condition"])].append(r)
    out = []
    for (b, m, c), rs in sorted(by.items()):
        roles = collections.Counter()
        for r in rs:
            for k, v in (r.get("tokens") or {}).items():
                roles[k] += v.get("in_tokens", 0) + v.get("out_tokens", 0)
        done = [r for r in rs if r.get("declared_done")]
        out.append({
            "bench": b, "model": m, "condition": c, "n": len(rs),
            "success": round(100 * sum(r["success"] for r in rs) / len(rs), 1),
            "admitted": round(100 * sum(bool(r.get("admitted")) for r in rs) / len(rs), 1) if "admitted" in rs[0] else None,
            "done_precision": round(100 * sum(r["success"] for r in done) / len(done), 1) if done else None,
            "median_total": int(st.median(tok(r) for r in rs)), "mean_total": int(st.mean(tok(r) for r in rs)),
            "median_out": int(st.median(tok(r, "out") for r in rs)), "mean_out": int(st.mean(tok(r, "out") for r in rs)),
            "calls": round(st.mean((r.get("total") or {}).get("calls", 0) for r in rs), 1),
            "by_role": {k: int(v / len(rs)) for k, v in roles.most_common()},
            "seconds": round(st.median(r.get("seconds", 0) for r in rs), 1),
        })
    return out


def paired(rows: list[dict], a: str, b: str) -> dict:
    idx = collections.defaultdict(dict)
    for r in rows:
        idx[(r["bench"], r["model"], r["task"], r["seed"])][r["condition"]] = r
    res = collections.defaultdict(list)
    for (bench, model, _t, _s), cs in idx.items():
        if a in cs and b in cs:
            res[(bench, model)].append((tok(cs[a]), tok(cs[b]), tok(cs[a], "out"), tok(cs[b], "out"),
                                        cs[a]["success"], cs[b]["success"]))
    out = {}
    for k, v in res.items():
        ta, tb = sum(x[0] for x in v), sum(x[1] for x in v)
        oa, ob = sum(x[2] for x in v), sum(x[3] for x in v)
        out[f"{k[0]}/{k[1]}"] = {"pairs": len(v), "total_ratio": round(tb / max(1, ta), 2),
                                 "out_ratio": round(ob / max(1, oa), 2),
                                 "median_pair_ratio": round(st.median(x[1] / max(1, x[0]) for x in v), 2),
                                 f"success_{a}": sum(x[4] for x in v), f"success_{b}": sum(x[5] for x in v)}
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("paths", nargs="+")
    ap.add_argument("--paired", default="autom2m,autom2m_base")
    ap.add_argument("--json", default="")
    a = ap.parse_args(argv)
    rows = load(a.paths)
    summ = summarize(rows)
    print(f"{'bench':13s} {'model':18s} {'condition':14s} {'n':>4s} {'succ':>5s} {'adm':>5s} {'prec':>5s} "
          f"{'med_tot':>8s} {'mean_tot':>8s} {'med_out':>7s} {'calls':>6s}  by role (mean tokens)")
    for s in summ:
        print(f"{s['bench']:13s} {s['model']:18s} {s['condition']:14s} {s['n']:4d} {s['success']:5.1f} "
              f"{(s['admitted'] if s['admitted'] is not None else float('nan')):5.1f} "
              f"{(s['done_precision'] if s['done_precision'] is not None else float('nan')):5.1f} "
              f"{s['median_total']:8d} {s['mean_total']:8d} {s['median_out']:7d} {s['calls']:6.1f}  {s['by_role']}")
    pa, pb = a.paired.split(",")
    pr = paired(rows, pa, pb)
    for k, v in pr.items():
        print(f"paired {pb}/{pa} {k}: {v}")
    if a.json:
        Path(a.json).write_text(json.dumps({"summary": summ, "paired": pr}, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
