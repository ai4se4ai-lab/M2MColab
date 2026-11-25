#!/usr/bin/env python3
"""AutoM2M multi-cluster controller (Alliance: fir, rorqual, narval).

Uses the ControlMaster sockets opened by hpc/scripts/ssh-session.sh; never
handles credentials. Every submission is appended to hpc/jobs.jsonl, and
every fetched result is kept under hpc/raw/<site>/ (raw) and merged into
hpc/processed/ (one raw-results file per benchmark and model).

  python hpc/scripts/fleet.py probe
  python hpc/scripts/fleet.py setup   [--sites fir]            # venvs, nltk data (login node, needs internet)
  python hpc/scripts/fleet.py deploy  [--sites fir,rorqual]    # code + exported benchmarks
  python hpc/scripts/fleet.py pull-model --site fir --model qwen2.5-coder:32b
  python hpc/scripts/fleet.py submit --site fir --model qwen2.5-coder:7b --bench classeval \
         --conditions autom2m,autom2m_base --seeds 1 --shard 0/4 --tag smoke --time 2:00:00
  python hpc/scripts/fleet.py status
  python hpc/scripts/fleet.py fetch [--tag smoke]
  python hpc/scripts/fleet.py merge --tag full
"""
from __future__ import annotations

import argparse
import json
import shlex
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CFG = json.loads((ROOT / "hpc/config/clusters.json").read_text())
HPC = ROOT / "hpc"
JOBS = HPC / "jobs.jsonl"
RAW = HPC / "raw"
PROCESSED = HPC / "processed"

# per-model serving parameters: (parallel requests, harness workers, context)
SERVE = {
    "qwen2.5-coder:7b": (16, 16, 16384),
    "qwen2.5-coder:32b": (8, 10, 16384),
    "devstral:24b": (8, 10, 16384),
    "deepseek-coder-v2:16b": (12, 12, 16384),
    "granite-code:20b": (8, 10, 8192),
    "codellama:13b": (12, 12, 16384),
    "starcoder2:15b": (12, 12, 16384),
}


def ssh_opts(site: str) -> list[str]:
    return ["-o", f"ControlPath={HPC}/.ssh/{site}", "-o", "ControlMaster=no",
            "-o", "BatchMode=yes", "-o", "ConnectTimeout=20"]


def target(site: str) -> str:
    return f"{CFG['username']}@{CFG['clusters'][site]['host']}"


def rsh(site: str, cmd: str, timeout: int = 120) -> tuple[int, str]:
    r = subprocess.run(["ssh", *ssh_opts(site), target(site), cmd], capture_output=True, text=True, timeout=timeout)
    return r.returncode, (r.stdout + r.stderr).strip()


def rsync(site: str, src: str, dst: str, *extra: str, timeout: int = 3600) -> int:
    e = "ssh " + " ".join(shlex.quote(x) for x in ssh_opts(site))
    return subprocess.run(["rsync", "-az", *extra, "-e", e, src, dst], timeout=timeout).returncode


def sites(arg: str | None) -> list[str]:
    return arg.split(",") if arg else list(CFG["clusters"])


def work(site: str) -> str:
    return CFG["clusters"][site]["work"]


def probe(a) -> None:
    out = {}
    for s in sites(a.sites):
        code, txt = rsh(s, "hostname; echo; "
                        "for b in " + " ".join(CFG["ollama_bin_candidates"]) + "; do [ -x $b ] && echo BIN:$b; done; "
                        "for d in " + " ".join(CFG["model_dir_candidates"]) + "; do [ -d $d/manifests ] && "
                        "echo MODELS:$d && find $d/manifests -mindepth 3 -maxdepth 4 -type f | sed 's|.*/library/||' | tr '\\n' ' ' && echo; done; "
                        "sinfo -h -o '%P %G %a %D' | grep -i gpu | head -8; "
                        "squeue -u $USER -h | wc -l; diskusage_report 2>/dev/null | grep -E 'scratch|project' | head -3; "
                        f"ls -d {work(s)}/venv {work(s)}/venv-sbx {work(s)}/code 2>/dev/null", timeout=120)
        out[s] = {"ok": code == 0, "report": txt}
        print(f"===== {s} (rc={code})\n{txt}\n")
    HPC.mkdir(parents=True, exist_ok=True)
    (HPC / "probe.json").write_text(json.dumps(out, indent=2))


SETUP = r"""
set -euo pipefail
module load StdEnv/2023 python/3.12 2>/dev/null || true
W={w}; mkdir -p $W/logs $W/results; cd $W
[ -x venv/bin/python ] || python -m venv venv
venv/bin/pip install -q --upgrade pip >/dev/null
venv/bin/pip install -q pyecore lark python-dotenv requests pydantic pyyaml
[ -x venv-sbx/bin/python ] || python -m venv venv-sbx
venv-sbx/bin/pip install -q --upgrade pip >/dev/null
for p in numpy pandas scipy nltk gensim lxml beautifulsoup4 openpyxl python-docx PyPDF2 reportlab pillow \
         python-dateutil pytz regex tqdm cloudpickle defusedxml smart_open; do
  # the Alliance wheelhouse first (prebuilt), PyPI binaries only for what it lacks
  venv-sbx/bin/pip install -q --no-index $p >/dev/null 2>&1 || venv-sbx/bin/pip install -q --prefer-binary $p >/dev/null 2>&1 \
    || echo "FAIL $p"
done
venv/bin/python -c "import pyecore, lark; print('venv ok')"
venv-sbx/bin/python -c "import numpy, nltk, gensim; print('venv-sbx ok')"
"""


def setup(a) -> None:
    for s in sites(a.sites):
        code, txt = rsh(s, SETUP.format(w=work(s)), timeout=3600)
        print(f"{s}: setup rc={code}\n{txt[-1500:]}")
        home = Path.home() / "nltk_data"
        if home.exists():
            print(f"{s}: nltk data rc={rsync(s, f'{home}/', f'{target(s)}:{work(s)}/nltk_data/')}")


def deploy(a) -> None:
    for s in sites(a.sites):
        w = work(s)
        rsh(s, f"mkdir -p {w}/code {w}/hpc/slurm {w}/logs {w}/results")
        rc1 = rsync(s, f"{ROOT}/", f"{target(s)}:{w}/code/", "--delete",
                    "--include=/src/***", "--include=/evaluation/***", "--include=/teams/***",
                    "--include=/data/", "--include=/data/benchmarks/***", "--include=/hpc/", "--include=/hpc/shards/***",
                    "--include=/pyproject.toml", "--exclude=__pycache__", "--exclude=*", )
        rc2 = rsync(s, f"{HPC}/slurm/", f"{target(s)}:{w}/hpc/slurm/")
        print(f"{s}: deploy rc={rc1},{rc2}")


def pull_model(a) -> None:
    """Pull a model into the shared model store from a login node (internet)."""
    s = a.site
    probe_info = json.loads((HPC / "probe.json").read_text()).get(s, {}).get("report", "")
    binp = a.ollama_bin or next((l[4:] for l in probe_info.splitlines() if l.startswith("BIN:")), "")
    mdir = a.models_dir or next((l[7:] for l in probe_info.splitlines() if l.startswith("MODELS:")), "")
    port = 31000 + (hash(a.model) % 1000)
    cmd = (f"export OLLAMA_MODELS={mdir} OLLAMA_HOST=127.0.0.1:{port} OLLAMA_NOPRUNE=1; "
           f"{binp} serve > /tmp/am2m_pull_{port}.log 2>&1 & SRV=$!; sleep 6; "
           f"{binp} pull {shlex.quote(a.model)} 2>&1 | tail -3; kill $SRV; wait $SRV 2>/dev/null; true")
    code, txt = rsh(s, cmd, timeout=7200)
    print(f"{s}: pull {a.model} rc={code}\n{txt[-800:]}")


def submit(a) -> None:
    s = a.site
    c = CFG["clusters"][s]
    w = c["work"]
    probe_info = json.loads((HPC / "probe.json").read_text()).get(s, {}).get("report", "")
    binp = a.ollama_bin or next((l[4:] for l in probe_info.splitlines() if l.startswith("BIN:")), "")
    mdir = a.models_dir or next((l[7:] for l in probe_info.splitlines() if l.startswith("MODELS:")), "")
    if not binp or not mdir:
        sys.exit(f"{s}: no ollama binary/model dir known; run probe or pass --ollama-bin/--models-dir")
    par, wk, ctx = SERVE.get(a.model, (8, 10, 16384))
    safe_model = a.model.replace(":", "_").replace("/", "_")
    i, n = a.shard.split("/")
    shard_tag = f"__sh{i}of{n}"
    taskfile = f"hpc/shards/{a.bench}_{i}of{n}.txt"
    if not (ROOT / taskfile).exists():
        sys.exit(f"{taskfile} missing: run `fleet.py shards --n {n}` and deploy")
    env = {"AM2M_WORK": w, "MODEL": a.model, "BENCH": a.bench, "CONDITIONS": a.conditions.replace(",", "+"),
           "SEEDS": a.seeds.replace(",", ":"), "TASKFILE": taskfile, "TAG": a.tag, "SHARD_TAG": shard_tag,
           "WORKERS": str(a.workers or wk), "PARALLEL": str(a.parallel or par), "NUM_CTX": str(ctx),
           "OLLAMA_BIN": binp, "OLLAMA_MODELS": mdir}
    if a.gate_budget:
        env["GATE_BUDGET"] = str(a.gate_budget)
    exports = "ALL," + ",".join(f"{k}={v}" for k, v in env.items())
    cmd = (f"cd {w}/logs && sbatch --parsable --account={c['gpu_account']} --gres={c['gres']} "
           f"--time={a.time} {('--dependency=' + a.dependency) if a.dependency else ''} "
           f"--job-name=am2m-{a.tag}-{safe_model[:14]}-{a.bench[:2]}{i} --export={shlex.quote(exports)} "
           f"{w}/hpc/slurm/am2m_matrix.sbatch")
    code, txt = rsh(s, cmd, timeout=180)
    job = next((l.split(";")[0] for l in reversed(txt.strip().splitlines()) if l.strip().split(";")[0].isdigit()),
               txt.strip()[-200:])
    rec = {"utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "site": s, "tag": a.tag, "model": a.model,
           "bench": a.bench, "conditions": a.conditions, "seeds": a.seeds, "shard": a.shard, "time": a.time,
           "rc": code, "job": job}
    HPC.mkdir(parents=True, exist_ok=True)
    with JOBS.open("a") as fh:
        fh.write(json.dumps(rec) + "\n")
    print(json.dumps(rec))


def shards(a) -> None:
    """Task lists per shard (round-robin over the benchmark's task order)."""
    sys.path[:0] = [str(ROOT / "src"), str(ROOT)]
    import os

    os.environ.setdefault("AM2M_DATA_DIR", str(ROOT / "data/benchmarks"))
    from evaluation.benchmarks import tasks as T

    d = HPC / "shards"
    d.mkdir(parents=True, exist_ok=True)
    for bench in ("classeval", "humanevalplus"):
        ids = [t.task_id for t in T.load(bench)]
        for i in range(a.n):
            (d / f"{bench}_{i}of{a.n}.txt").write_text("\n".join(ids[i::a.n]) + "\n")
    print(f"wrote {2 * a.n} shard files to {d}")


def status(a) -> None:
    for s in sites(a.sites):
        code, txt = rsh(s, "squeue -u $USER -h -o '%i %j %T %M %l %R' | grep am2m || true; "
                        "sacct -u $USER -S now-2days -n -X -o JobID,JobName%34,State,Elapsed | grep am2m | tail -60")
        print(f"===== {s}\n{txt}\n")


def fetch(a) -> None:
    for s in sites(a.sites):
        w = work(s)
        dst = RAW / s
        dst.mkdir(parents=True, exist_ok=True)
        src = f"{target(s)}:{w}/results/" + (f"{a.tag}/" if a.tag else "")
        rc = rsync(s, src, f"{dst}/" + (f"{a.tag}/" if a.tag else ""))
        rc2 = rsync(s, f"{target(s)}:{w}/logs/", f"{dst}/logs/", "--include=*.out", "--include=*/", "--exclude=*")
        print(f"{s}: fetch rc={rc},{rc2}")


def merge(a) -> None:
    """One raw-results file per benchmark and model over all sites and shards
    (first record of a (task, condition, seed) wins; error rows are dropped
    when a completed row exists)."""
    out_dir = PROCESSED / a.tag / "raw"
    out_dir.mkdir(parents=True, exist_ok=True)
    groups: dict[str, dict] = {}
    for f in sorted(RAW.glob(f"*/{a.tag}/raw/*.jsonl")):
        name = f.name.split("__sh")[0].removesuffix(".jsonl")
        g = groups.setdefault(name, {})
        for line in f.read_text().splitlines():
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            key = (r.get("task"), r.get("condition"), r.get("seed"))
            if key not in g or (g[key].get("status") == "error" and r.get("status") != "error"):
                g[key] = r
    for name, g in groups.items():
        (out_dir / f"{name}.jsonl").write_text("\n".join(json.dumps(r) for r in g.values()) + "\n")
        print(f"{name}: {len(g)} runs")


def main() -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("probe", "setup", "deploy", "status", "fetch"):
        p = sub.add_parser(name)
        p.add_argument("--sites")
        p.add_argument("--tag")
    p = sub.add_parser("merge")
    p.add_argument("--tag", required=True)
    p = sub.add_parser("shards")
    p.add_argument("--n", type=int, default=4)
    p = sub.add_parser("pull-model")
    p.add_argument("--site", required=True)
    p.add_argument("--model", required=True)
    p.add_argument("--ollama-bin")
    p.add_argument("--models-dir")
    p = sub.add_parser("submit")
    p.add_argument("--site", required=True)
    p.add_argument("--model", required=True)
    p.add_argument("--bench", required=True, choices=["classeval", "humanevalplus"])
    p.add_argument("--conditions", required=True)
    p.add_argument("--seeds", default="1")
    p.add_argument("--shard", default="0/1")
    p.add_argument("--tag", required=True)
    p.add_argument("--time", default="3:00:00")
    p.add_argument("--workers", type=int, default=0)
    p.add_argument("--parallel", type=int, default=0)
    p.add_argument("--gate-budget", type=int, default=0)
    p.add_argument("--dependency", default="")
    p.add_argument("--ollama-bin")
    p.add_argument("--models-dir")
    a = ap.parse_args()
    {"probe": probe, "setup": setup, "deploy": deploy, "pull-model": pull_model, "submit": submit, "status": status,
     "fetch": fetch, "merge": merge, "shards": shards}[a.cmd](a)
    return 0


if __name__ == "__main__":
    sys.exit(main())
