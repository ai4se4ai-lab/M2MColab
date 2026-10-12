"""RQ2: builder v1 (one proposal, full-team revisions) versus builder v2
(schema-constrained, checker-ranked proposals, localized repair) on the same
tasks and seeds; admission only (no team is run).

    python -m evaluation.rq2.builder_ablation --tasks first:30 --seeds 1 --workers 12
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def run_one(task_id: str, builder: str, model: str, seed: int) -> dict:
    from autom2m.loop import AutoM2M
    from evaluation.benchmarks import tasks as T
    from evaluation.harness.workbench import TaskWorkbench
    from evaluation.run_matrix import _safe, make_llm, team_shape

    task = {t.task_id: t for t in T.load("classeval")}[task_id]
    llm = make_llm(model, seed, budget=None)
    wd = Path(os.environ.get("TMPDIR", "/tmp")) / f"bab_{builder}_{_safe(task_id)}_{seed}"
    try:
        out = AutoM2M(llm, builder=builder, workdir=wd).solve(task, task.prompt, TaskWorkbench(task), admission_only=True)
    finally:
        shutil.rmtree(wd, ignore_errors=True)
    return {"task": task_id, "builder": builder, "seed": seed, "admitted": out.admitted_round is not None,
            "admitted_round": out.admitted_round, "violations_per_round": [len(d) for d in out.diagnostics],
            "first_violation": out.first_violation, "checks": out.checks, "unparsable": out.unparsable,
            "builder_tokens": llm.totals("builder")["out_tokens"], "seconds": round(llm.totals("builder")["seconds"], 1),
            "team_shape": team_shape(out.final_team) if out.admitted_round is not None else None,
            "final_diags": (out.diagnostics or [[]])[-1][:6]}


def main(argv=None) -> int:
    from evaluation.run_matrix import select_tasks

    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="qwen2.5-coder:7b")
    ap.add_argument("--tasks", default="first:30")
    ap.add_argument("--seeds", default="1")
    ap.add_argument("--builders", default="v1,v2")
    ap.add_argument("--workers", type=int, default=12)
    a = ap.parse_args(argv)
    out = ROOT / "results" / "rq2" / f"builder_ablation__{a.model.replace(':', '_')}.jsonl"
    out.parent.mkdir(parents=True, exist_ok=True)
    done = set()
    if out.exists():
        done = {(r["task"], r["builder"], r["seed"]) for r in map(json.loads, out.read_text().splitlines()) if "error" not in r}
    jobs = [(t, b, a.model, s) for s in map(int, a.seeds.split(",")) for t in select_tasks("classeval", a.tasks)
            for b in a.builders.split(",") if (t, b, s) not in done]
    print(f"{len(jobs)} admission sessions", flush=True)
    with ProcessPoolExecutor(a.workers) as ex, out.open("a") as fh:
        futs = {ex.submit(run_one, *j): j for j in jobs}
        for f in as_completed(futs):
            j = futs[f]
            try:
                r = f.result()
            except Exception as exc:  # noqa: BLE001
                r = {"task": j[0], "builder": j[1], "seed": j[3], "error": f"{type(exc).__name__}: {exc}"}
            fh.write(json.dumps(r) + "\n")
            fh.flush()
            print(r["task"], r["builder"], r.get("admitted"), r.get("violations_per_round"), r.get("error", ""), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
