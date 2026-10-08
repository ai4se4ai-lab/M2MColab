"""Run conditions x tasks x seeds for one model; resumable.

    python -m evaluation.run_matrix --bench classeval --model qwen2.5-coder:7b \
        --conditions single,free,critic,schema,typed_unchecked,autom2m --seeds 1,2,3 --workers 4

One summary line per run goes to results/raw/<bench>__<model>.jsonl; the
full record (transcript / typed-team outcome, final code) to
results/runs/<bench>/<model>/<condition>/<task>__s<seed>.json.
"""
from __future__ import annotations

import argparse
import json
import os
import time
import traceback
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "results"
OLLAMA = os.environ.get("AM2M_OLLAMA", "http://127.0.0.1:11435")
BUDGET_OUT = int(os.environ.get("AM2M_BUDGET_OUT", "24000"))  # generated tokens per run, every condition
CONDITIONS = ["single", "free", "critic", "schema", "typed_unchecked", "autom2m", "typed_ref"]


def _safe(s: str) -> str:
    return s.replace(":", "_").replace("/", "_")


def run_one(bench: str, task_id: str, condition: str, model: str, seed: int, temperature: float = 0.6) -> dict:
    from agentm2m.auto.loop import AutoM2M
    from agentm2m.llm.metered import MeteredBackend
    from agentm2m.llm.ollama_backend import OllamaBackend
    from evaluation.benchmarks import tasks as T
    from evaluation.conditions import baselines as B
    from evaluation.harness.workbench import TaskWorkbench

    task = {t.task_id: t for t in T.load(bench)}[task_id]
    llm = MeteredBackend(OllamaBackend(OLLAMA, model, seed=seed, num_ctx=16384, timeout=900, max_tokens=3072),
                         budget_out=BUDGET_OUT)
    t0 = time.time()
    detail: dict = {}
    status, code = "done", ""
    try:
        if condition in ("single", "free", "critic", "schema"):
            rec = {"single": B.run_single, "free": B.run_free, "critic": B.run_critic, "schema": B.run_schema}[condition](
                task, llm, temperature=temperature)
            code, status = rec.code, rec.status
            detail = {"transcript": rec.transcript, "team": rec.team, "extra": rec.extra}
        else:
            wb = TaskWorkbench(task)
            initial = None
            if condition == "typed_ref":
                initial = json.loads((ROOT / "teams" / "classeval_reference.json").read_text())
            am = AutoM2M(llm, temperature=temperature, check_enabled=condition != "typed_unchecked",
                         repair_enabled=condition in ("autom2m", "typed_ref"),
                         workdir=Path(os.environ.get("TMPDIR", "/tmp")) / f"am2m_{_safe(model)}_{condition}_{_safe(task_id)}_{seed}")
            out = am.solve(task, task.prompt, wb, initial_team=initial)
            status = out.status
            bodies = {k: (T.extract_function(v, k) or v) for k, v in out.deliverables.items()}
            code = T.assemble(task, bodies) if bodies else ""
            detail = out.to_dict()
    except Exception as exc:  # noqa: BLE001
        status = "error"
        detail = {"error": f"{type(exc).__name__}: {exc}", "trace": traceback.format_exc()[-3000:]}
    secs = time.time() - t0
    sc = T.score(task, code)
    row = {
        "bench": bench, "task": task_id, "condition": condition, "model": model, "seed": seed,
        "status": status, "success": bool(sc.get("success")), "test_pass_rate": float(sc.get("test_pass_rate", 0.0)),
        "tests_run": sc.get("tests_run"), "tests_passed": sc.get("tests_passed"),
        "seconds": round(secs, 1), "tokens": llm.by_role(), "total": llm.totals(), "budget_hit": llm.budget_hit,
    }
    if condition in ("autom2m", "typed_unchecked", "typed_ref"):
        row.update(admission_rounds=detail.get("admission_rounds"), phi=detail.get("phi"),
                   repairs=len(detail.get("repairs") or []),
                   fault_classes=[f.get("fault_class") for f in detail.get("fault_reports") or []],
                   first_diags=(detail.get("diagnostics") or [[]])[0])
    out_dir = RESULTS / "runs" / bench / _safe(model) / condition
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / f"{_safe(task_id)}__s{seed}.json").write_text(
        json.dumps({**row, "code": code, "score": sc, "detail": detail, "calls": llm.dump()}, default=str))
    return row


def done_keys(path: Path) -> set:
    keys = set()
    if path.exists():
        for line in path.read_text().splitlines():
            try:
                r = json.loads(line)
                if r.get("status") != "error":
                    keys.add((r["task"], r["condition"], r["seed"]))
            except (json.JSONDecodeError, KeyError):
                pass
    return keys


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--bench", required=True, choices=["classeval", "humanevalplus"])
    ap.add_argument("--model", required=True)
    ap.add_argument("--conditions", default=",".join(CONDITIONS[:6]))
    ap.add_argument("--seeds", default="1,2,3")
    ap.add_argument("--tasks", default="", help="comma list of task ids, or 'first:N', or 'file:path'")
    ap.add_argument("--workers", type=int, default=2)
    ap.add_argument("--tag", default="")
    a = ap.parse_args(argv)
    from evaluation.benchmarks import tasks as T

    all_ids = [t.task_id for t in T.load(a.bench)]
    if a.tasks.startswith("first:"):
        ids = all_ids[: int(a.tasks.split(":")[1])]
    elif a.tasks.startswith("file:"):
        ids = [l.strip() for l in Path(a.tasks[5:]).read_text().splitlines() if l.strip()]
    elif a.tasks:
        ids = a.tasks.split(",")
    else:
        ids = all_ids
    conds = a.conditions.split(",")
    seeds = [int(s) for s in a.seeds.split(",")]
    out = RESULTS / "raw" / f"{a.bench}__{_safe(a.model)}{a.tag}.jsonl"
    out.parent.mkdir(parents=True, exist_ok=True)
    done = done_keys(out)
    # seed-major order: complete seed 1 everywhere before seed 2 (graceful truncation)
    jobs = [(a.bench, t, c, a.model, s) for s in seeds for t in ids for c in conds if (t, c, s) not in done]
    print(f"{len(jobs)} runs to do ({len(done)} already done) -> {out}", flush=True)
    t0 = time.time()
    with ProcessPoolExecutor(a.workers) as ex, out.open("a") as fh:
        futs = {ex.submit(run_one, *j): j for j in jobs}
        for i, f in enumerate(as_completed(futs), 1):
            j = futs[f]
            try:
                row = f.result()
            except Exception as exc:  # noqa: BLE001
                row = {"bench": j[0], "task": j[1], "condition": j[2], "model": j[3], "seed": j[4], "status": "error",
                       "success": False, "error": str(exc)[:300]}
            fh.write(json.dumps(row) + "\n")
            fh.flush()
            el = time.time() - t0
            print(f"[{i}/{len(jobs)} {el/60:.0f}m eta {el/i*(len(jobs)-i)/3600:.1f}h] {row['task']} {row['condition']} "
                  f"s{row['seed']} {row['status']} ok={row['success']} rate={row.get('test_pass_rate', 0):.2f} "
                  f"{row.get('seconds', 0)}s out_tok={row.get('total', {}).get('out_tokens')}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
