"""hpc/scripts/run_step.py — rerun a single study module by hand, with a run's paths.

Use inside an interactive allocation (salloc) after `source hpc/slurm/common.sh` has set up
the venv and Ollama, or for LLM-free modules (analysis, rq2.mutate, rq2.scale) in any job:

    python hpc/scripts/run_step.py <RUN_ID> rq2.mutate
    python hpc/scripts/run_step.py <RUN_ID> run_matrix --bench classeval --model qwen2.5-coder:7b \
        --conditions autom2m --seeds 1 --tasks first:3
    python hpc/scripts/run_step.py <RUN_ID> analysis.analyze

The module name is relative to the repository's evaluation/ package; outputs go to
the run's results directory ($WORK/runs/<RUN_ID>/results), never into the repository.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

HPC = Path(__file__).resolve().parents[1]


def main() -> int:
    if len(sys.argv) < 3:
        print(__doc__)
        return 1
    run_id, module, rest = sys.argv[1], sys.argv[2], sys.argv[3:]
    sys.path.insert(0, str(HPC / "scripts"))
    from progress import cfg  # same parsing of the frozen run configuration

    c = cfg(run_id)
    work = Path(c.get("WORK") or Path(os.environ.get("SCRATCH", "")) / "autom2m_hpc")
    env = dict(os.environ)
    env.setdefault("AM2M_RESULTS", str(work / "runs" / run_id / "results"))
    env.setdefault("AM2M_DATA", c.get("AM2M_DATA", str(work / "data")))
    env.setdefault("AM2M_REPO", str(HPC.parent))
    env.setdefault("HF_HOME", c.get("HF_HOME", str(work / "hf_cache")))
    env.setdefault("HF_DATASETS_OFFLINE", "1")
    env.setdefault("HF_HUB_OFFLINE", "1")
    env.setdefault("AM2M_BUILDER", c.get("AM2M_BUILDER", "v2"))
    for k in ("AM2M_CODERS", "AM2M_CRITICS", "AM2M_SEEDER", "AM2M_JUDGE"):
        if c.get(k):
            env.setdefault(k, c[k])
    res = Path(env["AM2M_RESULTS"])
    env.setdefault("AM2M_DOCS_OUT", str(res / "docs_out"))
    env.setdefault("AM2M_WEB_OUT", str(res / "web" / "results.json"))
    env.setdefault("AM2M_REPORT", str(res / "report.md"))
    env["PYTHONPATH"] = os.pathsep.join([str(HPC.parent), str(HPC.parent / "src"), env.get("PYTHONPATH", "")]).rstrip(os.pathsep)
    print(f"[run_step] evaluation.{module} -> {env['AM2M_RESULTS']}", flush=True)
    return subprocess.run([sys.executable, "-m", f"evaluation.{module}", *rest], env=env, cwd=str(HPC.parent)).returncode


if __name__ == "__main__":
    raise SystemExit(main())
