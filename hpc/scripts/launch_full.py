#!/usr/bin/env python3
"""Submit the full experiment matrix (phase 1: every condition but Single-Gate;
phase 2: Single-Gate with AutoM2M's median output tokens as budget).

    python hpc/scripts/launch_full.py phase1 [--dry]
    python hpc/scripts/launch_full.py phase2 [--dry]     # after phase 1 is fetched and merged
    python hpc/scripts/launch_full.py missing            # shards with unfinished runs (resubmit with phase1/phase2)

Each (model, benchmark, shard) is one job; jobs are resumable, so a timed-out
shard is simply submitted again.
"""
from __future__ import annotations

import argparse
import json
import statistics
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TAG = "full"
SEEDS = "1,2,3"
SHARDS = 4
PHASE1 = "single,free,critic,schema,typed_nc,autom2m,typed_ref,autom2m_base"
# model -> (site, wall time per shard)
PLAN = {
    "qwen2.5-coder:7b": ("fir", "8:00:00"),
    "qwen2.5-coder:32b": ("fir", "24:00:00"),
    "devstral:24b": ("fir", "20:00:00"),
    "deepseek-coder-v2:16b": ("rorqual", "12:00:00"),
    "granite-code:20b": ("narval", "16:00:00"),
    "codellama:13b": ("narval", "12:00:00"),
    "starcoder2:15b": ("rorqual", "12:00:00"),
}
BENCH_TASKS = {"classeval": 100, "humanevalplus": 164}


def fleet(*args: str, dry: bool = False) -> None:
    cmd = [sys.executable, str(ROOT / "hpc/scripts/fleet.py"), *args]
    print(" ".join(cmd))
    if not dry:
        subprocess.run(cmd, check=False, timeout=300)


def safe(m: str) -> str:
    return m.replace(":", "_").replace("/", "_")


def merged(bench: str, model: str) -> list[dict]:
    p = ROOT / "hpc/processed" / TAG / "raw" / f"{bench}__{safe(model)}.jsonl"
    return [json.loads(l) for l in p.read_text().splitlines() if l.strip()] if p.exists() else []


def phase1(a) -> None:
    for model, (site, wall) in PLAN.items():
        for bench in BENCH_TASKS:
            for i in range(SHARDS):
                fleet("submit", "--site", site, "--model", model, "--bench", bench, "--conditions", PHASE1,
                      "--seeds", SEEDS, "--shard", f"{i}/{SHARDS}", "--tag", TAG, "--time", wall, dry=a.dry)


def phase2(a) -> None:
    for model, (site, _wall) in PLAN.items():
        for bench in BENCH_TASKS:
            toks = [r["total"]["out_tokens"] for r in merged(bench, model)
                    if r.get("condition") == "autom2m" and r.get("status") != "error"]
            if len(toks) < 20:
                print(f"skip {model} {bench}: only {len(toks)} AutoM2M runs merged")
                continue
            budget = int(statistics.median(toks))
            for i in range(SHARDS):
                fleet("submit", "--site", site, "--model", model, "--bench", bench, "--conditions", "single_gate",
                      "--seeds", SEEDS, "--shard", f"{i}/{SHARDS}", "--tag", TAG, "--time", "6:00:00",
                      "--gate-budget", str(budget), dry=a.dry)


def missing(_a) -> None:
    conds = PHASE1.split(",") + ["single_gate"]
    for model in PLAN:
        for bench, n in BENCH_TASKS.items():
            rows = merged(bench, model)
            done = {(r["task"], r["condition"], r["seed"]) for r in rows if r.get("status") != "error"}
            want = n * len(conds) * len(SEEDS.split(","))
            print(f"{model:22s} {bench:13s} {len(done):6d}/{want}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("phase", choices=["phase1", "phase2", "missing"])
    ap.add_argument("--dry", action="store_true")
    a = ap.parse_args()
    {"phase1": phase1, "phase2": phase2, "missing": missing}[a.phase](a)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
