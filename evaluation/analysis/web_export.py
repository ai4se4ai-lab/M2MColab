"""Condense results/summary.json into web/src/data/results.json, the data the
website's Results section renders (so the site always shows the current
numbers; missing studies render as "pending").

    python -m evaluation.analysis.web_export
"""
from __future__ import annotations

import datetime as dt
import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RESULTS = Path(os.environ.get("AM2M_RESULTS", ROOT / "results"))
OUT = ROOT / "web" / "src" / "data" / "results.json"
CONDS = ["single", "single_gate", "free", "critic", "schema", "typed_nc", "autom2m", "typed_ref"]


def _g(d, *keys, default=None):
    for k in keys:
        if not isinstance(d, dict) or k not in d:
            return default
        d = d[k]
    return d


def main() -> int:
    s = json.loads((RESULTS / "summary.json").read_text()) if (RESULTS / "summary.json").exists() else {}
    runs = _g(s, "runs", default={}) or {}
    by_cond = runs.get("by_condition") or {}
    counts = {}
    for cond, per_bench in by_cond.items():
        for bench, n in (per_bench or {}).items():
            counts.setdefault(bench, {})[cond] = n
    succ = _g(s, "rq3", "success", default={}) or {}
    models = sorted({m for b in ("classeval", "humanevalplus") for m in (succ.get(b) or {})
                     if m not in ("mean", "holm_within_family_significant", "tests", "holm_over_all_significant")})
    success = {}
    for b in ("classeval", "humanevalplus"):
        success[b] = {"mean": _g(succ, b, "mean", default={}),
                      "per_model": {m: _g(succ, b, m, "success", default={}) for m in models},
                      "holm_p": {m: _g(succ, b, m, "holm_p", default={}) for m in models},
                      "cochran_p": {m: _g(succ, b, m, "cochran_p") for m in models},
                      "seeds": {m: _g(succ, b, m, "seeds") for m in models}}
    done = _g(s, "rq3", "done_cost", default={}) or {}
    out = {
        "generated": dt.date.today().isoformat(),
        "models": models,
        "run_counts": counts,
        "rq1": {k: {"n": v.get("n"), "share": v.get("composition_share"), "ci": v.get("ci"),
                    "with_secondary": v.get("with_secondary"), "kappa": v.get("kappa_coders"),
                    "by_code": v.get("by_code")}
                for k, v in (_g(s, "rq1", default={}) or {}).items() if isinstance(v, dict) and "n" in v},
        "audit": _g(s, "rq1", "audit", "algorithm_generated"),
        "mutation": _g(s, "rq2", "mutation"),
        "independent": _g(s, "rq2", "independent"),
        "natural_mutants": _g(s, "rq2", "natural_mutants", "operators", "detach_goal"),
        "admission": _g(s, "rq2", "admission"),
        "scale": _g(s, "rq2", "scale"),
        "success": success,
        "gee": {"vs_free": _g(s, "rq3", "success", "gee_vs_free"), "h3b": _g(s, "rq3", "success", "h3b")},
        "compfail": _g(s, "rq3", "compfail"),
        "done": {b: {c: {k: (v or {}).get(k) for k in ("precision", "precision_ci", "lift", "recall", "f1", "median_tokens",
                                                      "median_seconds", "pass_per_mtok", "base_rate")}
                     for c, v in (done.get(b) or {}).items()} for b in done},
        "token_shares": _g(done, "classeval", "autom2m", "token_shares"),
        "rq4": _g(s, "rq4"),
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, indent=1, default=str))
    print("wrote", OUT)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
