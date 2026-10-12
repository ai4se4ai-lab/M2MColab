"""RQ4, checked repair versus rebuilding (paper Sec. 4.6, Repair).

For failed AutoM2M runs, the admitted team is run again until phi fails; then
the same fault reports drive (a) one checked delta (the repair planner,
applied in place / by extension / by rebuild after the checker admits it) and
(b) rebuilding the team from scratch: the builder proposes a new team for the
task given the fault reports, which is admitted by the checker and run from
an empty state, as a free-form builder must. Both are scored on the hidden
tests; tokens and preserved accepted values are recorded.

    python -m evaluation.rq4.repair --n 100
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import random
import shutil
import time
import traceback
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def candidates(model: str, n: int, seed: int = 0) -> list[tuple[str, str, int]]:
    from evaluation.run_matrix import _safe

    out = []
    for bench in ("classeval", "humanevalplus"):
        for f in sorted(glob.glob(str(ROOT / "results" / "runs" / bench / _safe(model) / "autom2m" / "*.json"))):
            d = json.loads(Path(f).read_text())
            if d.get("status") == "failed" and (d.get("detail") or {}).get("final_team"):
                out.append((bench, d["task"], d["seed"]))
    random.Random(seed).shuffle(out)
    return out[:n]


def run_one(bench: str, task_id: str, seed: int, model: str) -> dict:
    from agenthot.compiler import compile_team
    from agenthot.session import Session
    from autom2m.attribution import attribute_all
    from autom2m.loop import AutoM2M
    from autom2m.prompts import propose_prompt
    from autom2m.typed_team import parse_team
    from autom2m.vlib import RunContext
    from evaluation.benchmarks import tasks as T
    from evaluation.harness.workbench import TaskWorkbench
    from evaluation.run_matrix import _safe, make_llm

    task = {t.task_id: t for t in T.load(bench)}[task_id]
    d = json.loads((ROOT / "results" / "runs" / bench / _safe(model) / "autom2m" / f"{_safe(task_id)}__s{seed}.json").read_text())
    team_json = d["detail"]["final_team"]
    workdir = Path(os.environ.get("TMPDIR", "/tmp")) / f"rq4rep_{bench}_{_safe(task_id)}_{seed}"
    llm = make_llm(model, seed + 100, budget=None)
    row = {"bench": bench, "task": task_id, "seed": seed}
    t0 = time.time()
    try:
        ct = compile_team(parse_team(team_json), task, workdir / "base")
        wb = TaskWorkbench(task)
        s = Session(ct, llm, RunContext(wb), k=3)
        wb.bind(s.current_bodies)
        llm.role = "binding"
        res = s.run()
        row["base_phi"] = res.phi
        if res.phi:
            return row
        llm.role = "attribution"
        reports, calls = attribute_all(s, res.failures)
        row["faults"] = [r.fault_class for r in reports]
        summaries = [r.summary() for r in reports]
        snap_tokens = llm.totals()["out_tokens"]
        # (a) one checked delta
        am = AutoM2M(llm, workdir=workdir / "repair")
        llm.role = "repair"
        s2, info = am._repair(s, reports, task)
        llm.role = "binding"
        res2 = s2.run()
        code = T.assemble(task, {k: (T.extract_function(v, k) or v) for k, v in res2.deliverables.items()})
        row["repair"] = {"info": info, "phi": res2.phi, "success": T.score(task, code)["success"],
                         "out_tokens": llm.totals()["out_tokens"] - snap_tokens,
                         "preserved": (info.get("kept") / info["before"]) if info.get("before") else None}
        # (b) rebuild from scratch with the same fault report
        t_before = llm.totals()["out_tokens"]
        builder = AutoM2M(llm, workdir=workdir / "rebuild", repair_enabled=False)
        prompt = propose_prompt(task.prompt, task.kind) + "\n\nA previous team for this task failed with these fault " \
            "reports; build a new team from scratch that avoids them:\n" + "\n".join(summaries)
        new_team = builder._builder(prompt, "rebuild")
        out = builder.solve(task, task.prompt, TaskWorkbench(task), initial_team=new_team)
        code_rb = T.assemble(task, {k: (T.extract_function(v, k) or v) for k, v in out.deliverables.items()}) \
            if out.deliverables else ""
        row["rebuild"] = {"status": out.status, "phi": out.phi, "success": T.score(task, code_rb)["success"] if code_rb else False,
                          "out_tokens": llm.totals()["out_tokens"] - t_before, "admitted": out.admitted_round is not None}
    except Exception as exc:  # noqa: BLE001
        row["error"] = f"{type(exc).__name__}: {exc}"
        row["trace"] = traceback.format_exc()[-1500:]
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
    row["seconds"] = round(time.time() - t0, 1)
    return row


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="qwen2.5-coder:7b")
    ap.add_argument("--n", type=int, default=100)
    ap.add_argument("--workers", type=int, default=8)
    a = ap.parse_args(argv)
    out = ROOT / "results" / "rq4" / f"repair__{a.model.replace(':', '_')}.jsonl"
    out.parent.mkdir(parents=True, exist_ok=True)
    done = set()
    if out.exists():
        done = {(json.loads(l)["bench"], json.loads(l)["task"], json.loads(l)["seed"]) for l in out.read_text().splitlines() if l.strip()}
    jobs = [c for c in candidates(a.model, a.n) if c not in done]
    print(f"{len(jobs)} failed AutoM2M runs to repair / rebuild", flush=True)
    with ProcessPoolExecutor(a.workers) as ex, out.open("a") as fh:
        futs = {ex.submit(run_one, b, t, s, a.model): (b, t, s) for b, t, s in jobs}
        for fu in as_completed(futs):
            b, t, s = futs[fu]
            try:
                row = fu.result()
            except Exception as exc:  # noqa: BLE001
                row = {"bench": b, "task": t, "seed": s, "error": str(exc)[:300]}
            fh.write(json.dumps(row, default=str) + "\n")
            fh.flush()
            print(t, s, "base_phi", row.get("base_phi"), "repair", (row.get("repair") or {}).get("success"),
                  "rebuild", (row.get("rebuild") or {}).get("success"), row.get("error", ""), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
