"""RQ3: decisive cause of every failing *executed* run, in every condition
(composition-caused failures, Table 9).

Every run is rendered into one transcript format (typed runs appear as
messages, one per accepted or rejected value; the condition is not named) and
labelled by two LLM coders with the RQ1 codebook; agreeing labels are kept,
disagreements resolved by a third pass with a fixed tie-break rule.
Non-admitted AutoM2M sessions are not coded: the analysis reports them under
three explicit rules (executed only; refused = composition; refused = other).

    python -m evaluation.rq3.code_runs --workers 12                       # every failing executed run
    python -m evaluation.rq3.code_runs --workers 12 --limit-per-cell 50   # stratified random sample

With --limit-per-cell N, each (benchmark, model, condition) cell contributes at most N failing executed runs. The
sample is seeded and independent of file order (runs are ordered by a hash of seed and run id), so a restarted job
continues the same sample; cells with at most N failures are coded completely. The sample design is written to
rq3/codes_runs_sample.json, and the analysis weights by it (evaluation/analysis/analyze.py: rq3_compfail).
"""
from __future__ import annotations

import argparse
import glob
import hashlib
import json
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from evaluation import llms
from evaluation.coding import code_item, render_run

ROOT = Path(__file__).resolve().parents[2]
RESULTS = Path(os.environ.get("AM2M_RESULTS", ROOT / "results"))
CONDS = ("single", "single_gate", "free", "critic", "schema", "typed_nc", "autom2m", "typed_ref")


def _safe(s: str) -> str:
    return s.replace(":", "_").replace("/", "_")


def _rank(seed: int, item_id: str) -> str:
    return hashlib.sha1(f"{seed}:{item_id}".encode()).hexdigest()


def _failing_from_raw() -> dict | None:
    """The failing executed runs per cell from the one-line-per-run files raw/<bench>__<model>.jsonl (seconds), or
    None when there are none. Equivalent to reading every run record (the last row of a run is its record), but it
    avoids opening ~44,000 large files on a parallel file system (about 90 minutes at the study's scale)."""
    from collections import defaultdict

    last: dict = {}
    for f in sorted(glob.glob(str(RESULTS / "raw" / "*.jsonl"))):
        for line in Path(f).read_text().splitlines():
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            last[(r["bench"], _safe(r["model"]), r["condition"], _safe(r["task"]), r["seed"])] = r
    if not last:
        return None
    cells = defaultdict(list)
    for (bench, model, cond, task, seed), r in sorted(last.items()):
        if cond not in CONDS or r.get("success") or r.get("status") in ("error", "not_admitted"):
            continue
        stem = f"{task}__s{seed}"  # the run record is written before its raw row, so the file exists
        cells[(bench, model, cond)].append({"id": f"{bench}/{model}/{cond}/{stem}", "bench": bench, "model": model,
                                            "condition": cond,
                                            "file": str(RESULTS / "runs" / bench / model / cond / f"{stem}.json")})
    return cells


def items(limit_per_cell: int | None = None, seed: int = 0) -> tuple[list[dict], dict]:
    """(runs to code, per-cell design {cell: {failing, sampled}}) over the failing executed runs."""
    from collections import defaultdict

    cells = _failing_from_raw()
    if cells is None:  # no raw files: read every run record
        cells = defaultdict(list)
        for f in sorted(glob.glob(str(RESULTS / "runs" / "*" / "*" / "*" / "*.json"))):
            p = Path(f)
            cond, model, bench = p.parent.name, p.parent.parent.name, p.parent.parent.parent.name
            if cond not in CONDS:
                continue
            d = json.loads(p.read_text())
            if d.get("success") or d.get("status") in ("error", "not_admitted"):
                continue
            cells[(bench, model, cond)].append({"id": f"{bench}/{model}/{cond}/{p.stem}", "bench": bench,
                                                "model": model, "condition": cond, "file": f})
    out, design = [], {}
    for cell, its in sorted(cells.items()):
        chosen = its
        if limit_per_cell:
            chosen = sorted(its, key=lambda it: _rank(seed, it["id"]))[:limit_per_cell]
        out += chosen
        design["/".join(cell)] = {"failing": len(its), "sampled": len(chosen)}
    return out, design


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--workers", type=int, default=12)
    ap.add_argument("--limit-per-cell", type=int, default=None,
                    help="seeded stratified random sample of at most N failing runs per (bench, model, condition)")
    ap.add_argument("--seed", type=int, default=0)
    a = ap.parse_args(argv)
    out = RESULTS / "rq3" / "codes_runs.jsonl"
    out.parent.mkdir(parents=True, exist_ok=True)
    selected, design = items(a.limit_per_cell, a.seed)
    (RESULTS / "rq3" / "codes_runs_sample.json").write_text(json.dumps(
        {"limit_per_cell": a.limit_per_cell, "seed": a.seed, "failing": sum(v["failing"] for v in design.values()),
         "sampled": sum(v["sampled"] for v in design.values()), "cells": design}, indent=1))
    keep = {it["id"] for it in selected}
    done = set()
    if out.exists():
        lines = [l for l in out.read_text().splitlines() if l.strip()]
        inside = [l for l in lines if json.loads(l)["id"] in keep]
        if len(inside) != len(lines):
            # codings of runs outside the sample (from an earlier uncapped start) are set aside, not analysed
            side = out.with_name("codes_runs_outside_sample.jsonl")
            with side.open("a") as fh:
                fh.writelines(l + "\n" for l in lines if json.loads(l)["id"] not in keep)
            out.write_text("".join(l + "\n" for l in inside))
            print(f"set aside {len(lines) - len(inside)} codings outside the sample -> {side.name}", flush=True)
        done = {json.loads(l)["id"] for l in inside}
    todo = [it for it in selected if it["id"] not in done]
    specs = llms.coders()
    print(f"{len(todo)} failing runs to code ({len(selected)} selected of "
          f"{sum(v['failing'] for v in design.values())} failing, {len(done)} already coded) with "
          f"{[llms.label(s) for s in specs]}", flush=True)

    def work(it):
        d = json.loads(Path(it["file"]).read_text())
        coders = [llms.make(m, s, max_tokens=256) for m, s in specs[:2]]
        res = code_item(coders, render_run(d))
        return {k: v for k, v in it.items() if k != "file"} | res

    with ThreadPoolExecutor(a.workers) as ex, out.open("a") as fh:
        for r in ex.map(work, todo):
            fh.write(json.dumps(r) + "\n")
            fh.flush()
            print(r["id"], r["coder_a"]["code"], r["coder_b"]["code"], "->", r["final"], flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
