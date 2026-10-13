"""hpc/scripts/manifest.py — record what a run used (stdlib only; called by submit_pipeline.sh).

Writes run_manifest.json: run id, time, git commit/branch, whether the code differs from
the commit (with a hash of the diff of src/, teams/ and evaluation/), a content hash of
the harness (evaluation/) and of the engine (src/), the frozen
configuration and model sizing. Model digests are added by the setup stage
($RUN_DIR/setup/model_digests.json) and merged by summarize.py.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import platform
import subprocess
import time
from pathlib import Path


def git(repo: Path, *args: str) -> str:
    try:
        return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, timeout=60).stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        return ""


def tree_hash(root: Path) -> str:
    h = hashlib.sha256()
    for p in sorted(root.rglob("*")):
        if p.is_file() and "__pycache__" not in p.parts and p.suffix in (".py", ".lark", ".json", ".yaml", ".agenthot", ".txt"):
            h.update(str(p.relative_to(root)).encode())
            h.update(p.read_bytes())
    return h.hexdigest()[:16]


def parse_env(path: Path) -> dict:
    out = {}
    for line in path.read_text().splitlines():
        line = line.split("#", 1)[0].strip() if not line.strip().startswith("#") else ""
        if "=" in line:
            k, v = line.split("=", 1)
            out[k.strip()] = v.strip().strip('"')
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-id", required=True)
    ap.add_argument("--hpc", required=True)
    ap.add_argument("--config", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    hpc = Path(a.hpc)
    repo = hpc.parent
    out = Path(a.out)
    prev = json.loads(out.read_text()) if out.exists() else {}
    man = {
        "run_id": a.run_id,
        "created": prev.get("created") or time.strftime("%F %T %Z"),
        "updated": time.strftime("%F %T %Z"),
        "submit_host": platform.node(),
        "git": {"commit": git(repo, "rev-parse", "HEAD"), "branch": git(repo, "rev-parse", "--abbrev-ref", "HEAD"),
                "dirty_paths": git(repo, "status", "--porcelain", "--", "src", "teams", "evaluation").splitlines(),
                "diff_sha256": hashlib.sha256(git(repo, "diff", "HEAD", "--", "src", "teams", "evaluation").encode()).hexdigest()[:16]},
        "content_sha256": {"src": tree_hash(repo / "src"), "evaluation": tree_hash(repo / "evaluation"),
                           "teams": tree_hash(repo / "teams")},
        "config": parse_env(Path(a.config)),
        "models_tsv": [l for l in (Path(a.config).parent / "models.tsv").read_text().splitlines() if l and not l.startswith("#")]
        if (Path(a.config).parent / "models.tsv").exists() else [],
        "submissions": prev.get("submissions", []) + [time.strftime("%F %T")],
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(man, indent=1))
    print(out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
