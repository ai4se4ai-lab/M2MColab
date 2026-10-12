"""RQ3: decisive cause of every failing *executed* run, in every condition
(composition-caused failures, Table 9).

Every run is rendered into one transcript format (typed runs appear as
messages, one per accepted or rejected value; the condition is not named) and
labelled by two LLM coders with the RQ1 codebook; agreeing labels are kept,
disagreements resolved by a third pass with a fixed tie-break rule.
Non-admitted AutoM2M sessions are not coded: the analysis reports them under
three explicit rules (executed only; refused = composition; refused = other).

    python -m evaluation.rq3.code_runs --workers 12
"""
from __future__ import annotations

import argparse
import glob
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from evaluation import llms
from evaluation.coding import code_item, render_run

ROOT = Path(__file__).resolve().parents[2]
CONDS = ("single", "single_gate", "free", "critic", "schema", "typed_nc", "autom2m", "typed_ref")


def items(limit_per_cell: int | None = None) -> list[dict]:
    out = []
    for f in sorted(glob.glob(str(ROOT / "results" / "runs" / "*" / "*" / "*" / "*.json"))):
        p = Path(f)
        cond, model, bench = p.parent.name, p.parent.parent.name, p.parent.parent.parent.name
        if cond not in CONDS:
            continue
        d = json.loads(p.read_text())
        if d.get("success") or d.get("status") in ("error", "not_admitted"):
            continue
        out.append({"id": f"{bench}/{model}/{cond}/{p.stem}", "bench": bench, "model": model, "condition": cond,
                    "file": f})
    if limit_per_cell:
        from collections import defaultdict

        cells = defaultdict(list)
        for it in out:
            cells[(it["bench"], it["model"], it["condition"])].append(it)
        out = [it for v in cells.values() for it in v[:limit_per_cell]]
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--workers", type=int, default=12)
    ap.add_argument("--limit-per-cell", type=int, default=None)
    a = ap.parse_args(argv)
    out = ROOT / "results" / "rq3" / "codes_runs.jsonl"
    out.parent.mkdir(parents=True, exist_ok=True)
    done = set()
    if out.exists():
        done = {json.loads(l)["id"] for l in out.read_text().splitlines() if l.strip()}
    todo = [it for it in items(a.limit_per_cell) if it["id"] not in done]
    specs = llms.coders()
    print(f"{len(todo)} failing runs to code with {[llms.label(s) for s in specs]}", flush=True)

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
