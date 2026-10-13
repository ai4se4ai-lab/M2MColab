"""hpc/scripts/prefetch.py — stage and verify the study's data (run by the 00_setup job).

    prefetch.py download   ClassEval + HumanEval+ into $HF_HOME, nltk data into ~/.cache/nltk_data
    prefetch.py verify     offline: task counts, Who&When files, third-party imports of the
                           benchmarks, and the hidden tests on every reference solution
                           (checks that the sandbox interpreter can run the benchmarks at all)
"""
from __future__ import annotations

import argparse
import ast
import importlib.util
import json
import os
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

NLTK_PACKAGES = ["punkt", "punkt_tab", "wordnet", "omw-1.4", "stopwords",
                 "averaged_perceptron_tagger", "averaged_perceptron_tagger_eng"]


def download() -> int:
    from datasets import load_dataset

    for name in ("FudanSELab/ClassEval", "evalplus/humanevalplus"):
        ds = load_dataset(name, split="test")
        print(f"{name}: {len(ds)} rows cached in {os.environ.get('HF_HOME')}", flush=True)
    import nltk

    target = os.environ.get("NLTK_DATA_DIR") or os.path.expanduser("~/.cache/nltk_data")
    for p in NLTK_PACKAGES:
        ok = nltk.download(p, download_dir=target, quiet=True)
        print(f"nltk {p}: {'ok' if ok else 'FAILED'} -> {target}", flush=True)
    return 0


def _imports(src: str) -> set[str]:
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return set()
    out = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            out |= {a.name.split(".")[0] for a in n.names}
        elif isinstance(n, ast.ImportFrom) and n.module and not n.level:
            out.add(n.module.split(".")[0])
    return out


def _score(args):
    bench, task_id = args
    from evaluation.benchmarks import tasks as T

    task = {t.task_id: t for t in T.load(bench)}[task_id]
    sc = T.score(task, task.canonical)
    return task_id, bool(sc.get("success")), (sc.get("error") or "")[:200]


def verify(a) -> int:
    from evaluation.benchmarks import tasks as T
    from evaluation.rq1.code_failures import WW as WHOWHEN  # honours AM2M_DATA

    rc = 0
    report: dict = {}
    for bench, n_exp in (("classeval", 100), ("humanevalplus", 164)):
        ts = T.load(bench)
        print(f"{bench}: {len(ts)} tasks (expected {n_exp})", flush=True)
        report[f"{bench}_tasks"] = len(ts)
        rc |= int(len(ts) != n_exp)
    for sub, n_exp in (("Algorithm-Generated", 126), ("Hand-Crafted", 58)):
        n = len(list((WHOWHEN / sub).glob("*.json")))
        print(f"Who&When {sub}: {n} files (expected {n_exp}) in {WHOWHEN}", flush=True)
        report[f"whowhen_{sub}"] = n
        rc |= int(n < n_exp)

    mods = set()
    for t in T.load("classeval") + T.load("humanevalplus"):
        mods |= _imports(t.imports or "") | _imports(t.hidden_test or "") | _imports(t.canonical or "")
    third = sorted(m for m in mods if m not in sys.stdlib_module_names and m not in ("solution",))
    missing = [m for m in third if importlib.util.find_spec(m) is None]
    print(f"third-party imports used by the benchmarks: {third}", flush=True)
    print(f"missing in the sandbox interpreter: {missing or 'none'}", flush=True)
    report["third_party_imports"], report["missing_imports"] = third, missing

    for bench, thr in (("classeval", a.min_classeval), ("humanevalplus", a.min_humaneval)):
        ids = [t.task_id for t in T.load(bench)]
        with ProcessPoolExecutor(a.workers) as ex:
            res = list(ex.map(_score, [(bench, i) for i in ids]))
        ok = sum(1 for _, s, _ in res if s)
        fails = [(i, e) for i, s, e in res if not s]
        share = ok / len(res)
        print(f"{bench}: reference solutions pass the hidden tests in {ok}/{len(res)} ({share:.1%}); "
              f"threshold {thr:.0%}", flush=True)
        for i, e in fails:
            print(f"   reference fails: {i}: {e}", flush=True)
        report[f"{bench}_canonical_pass"] = share
        report[f"{bench}_canonical_failures"] = fails
        rc |= int(share < thr)
    if a.out:
        Path(a.out).parent.mkdir(parents=True, exist_ok=True)
        Path(a.out).write_text(json.dumps(report, indent=1))
    print("VERIFY", "OK" if rc == 0 else "FAILED", flush=True)
    return rc


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["download", "verify"])
    ap.add_argument("--min-classeval", type=float, default=0.95)
    ap.add_argument("--min-humaneval", type=float, default=0.98)
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--out", default="")
    a = ap.parse_args()
    return download() if a.cmd == "download" else verify(a)


if __name__ == "__main__":
    raise SystemExit(main())
