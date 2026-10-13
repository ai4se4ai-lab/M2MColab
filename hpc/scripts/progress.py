"""hpc/scripts/progress.py — how far a run has got (stdlib only; safe on the login node).

    python3 hpc/scripts/progress.py <RUN_ID>

Reads $WORK/runs/<RUN_ID>/results (raw/*.jsonl and rq*/), the frozen run configuration
and the step ledger, and prints: matrix runs done/expected per model and benchmark
(and per condition), runs in error, success so far, the RQ output files with their
sizes, and failed steps. Reads files only; never queries Slurm in a loop.
"""
from __future__ import annotations

import json
import os
import sys
from collections import Counter, defaultdict
from pathlib import Path

HPC = Path(__file__).resolve().parents[1]
N_TASKS = {"classeval": 100, "humanevalplus": 164}


def cfg(run_id: str) -> dict:
    """The run's frozen configuration, evaluated by bash exactly as the jobs see it."""
    import subprocess

    f = HPC / "logs" / run_id / "run_config.env"
    f = f if f.exists() else HPC / "config" / "pipeline.env"
    out = subprocess.run(["bash", "-c", f'set -a; source "{f}" >/dev/null 2>&1; env -0'],
                         capture_output=True, check=False).stdout.decode(errors="replace")
    return dict(kv.split("=", 1) for kv in out.split("\0") if "=" in kv)


def rows(p: Path) -> list[dict]:
    out = []
    for line in p.read_text().splitlines():
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            pass
    return out


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 1
    run_id = sys.argv[1]
    c = cfg(run_id)
    work = Path(c.get("WORK") or Path(os.environ.get("SCRATCH", "")) / "autom2m_hpc")
    res = Path(os.environ.get("AM2M_RESULTS") or work / "runs" / run_id / "results")
    models = c.get("MODELS", "").split()
    conds = c.get("CONDITIONS", "").split(",")
    seeds = c.get("SEEDS", "1,2,3").split(",")
    tasks_spec = c.get("TASKS", "")
    print(f"RUN {run_id}   results: {res}")
    if tasks_spec:
        print(f"(TASKS={tasks_spec}: expected counts below assume all tasks)")
    tot_done = tot_exp = 0
    print(f"\n{'model':24s} {'bench':14s} {'done':>12s} {'error':>6s} {'success':>8s}   per condition (done)")
    for m in models:
        for b in c.get("BENCHES", "classeval humanevalplus").split():
            exp = N_TASKS[b] * len(conds) * len(seeds)
            p = res / "raw" / f"{b}__{m.replace(':', '_').replace('/', '_')}.jsonl"
            rs = rows(p) if p.exists() else []
            last = {}
            for r in rs:
                last[(r.get("task"), r.get("condition"), r.get("seed"))] = r
            ok = [r for r in last.values() if r.get("status") != "error" and r.get("condition") in conds]
            err = sum(1 for r in last.values() if r.get("status") == "error")
            succ = sum(1 for r in ok if r.get("success"))
            per = Counter(r["condition"] for r in ok)
            tot_done += len(ok)
            tot_exp += exp
            print(f"{m:24s} {b:14s} {len(ok):>5d}/{exp:<6d} {err:>6d} {(succ / len(ok) if ok else 0):>8.1%}   "
                  + " ".join(f"{k}={per.get(k, 0)}" for k in conds))
    print(f"\nmatrix total: {tot_done}/{tot_exp} ({tot_done / max(tot_exp, 1):.1%})")

    print("\nRQ outputs:")
    for sub in ("rq1", "rq2", "rq3", "rq4", "data"):
        d = res / sub
        if d.exists():
            for f in sorted(d.iterdir()):
                n = sum(1 for _ in f.open()) if f.suffix == ".jsonl" else None
                print(f"  {sub}/{f.name:44s} {f.stat().st_size / 1024:>9.0f} KiB" + (f"  {n} lines" if n is not None else ""))
    for f in ("summary.json", "report.md"):
        print(f"  {f:48s} {'present' if (res / f).exists() else '-'}")

    ledger = HPC / "logs" / run_id / "steps.tsv"
    if ledger.exists():
        last = {}
        for line in ledger.read_text().splitlines()[1:]:
            f = line.split("\t")
            if len(f) >= 9:
                last[(f[3], f[4])] = f
        fails = [f for f in last.values() if f[8] != "0"]
        print(f"\nsteps: {len(last)} run, {len(fails)} whose latest attempt failed")
        for f in fails:
            print(f"  FAILED rc={f[8]}  {f[3]} / {f[4]}  (job {f[1]}, log: logs/{run_id}/steps/{f[3]}/{f[4]}.log)")
    jf = HPC / "logs" / run_id / "jobs_finished.tsv"
    if jf.exists():
        st = defaultdict(list)
        for line in jf.read_text().splitlines():
            f = line.split("\t")
            if len(f) >= 5:
                st[f[4].split("(")[0]].append(f[2])
        print("\njob endings: " + ", ".join(f"{k}: {len(v)}" for k, v in st.items()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
