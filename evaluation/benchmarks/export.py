"""Export the benchmark tasks to JSONL (for machines without the datasets).

    python -m evaluation.benchmarks.export --out data/benchmarks
"""
from __future__ import annotations

import argparse
import dataclasses
import hashlib
import json
from pathlib import Path


def main(argv=None) -> int:
    from evaluation.benchmarks import tasks as T

    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="data/benchmarks")
    a = ap.parse_args(argv)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    import os

    os.environ.pop("AM2M_DATA_DIR", None)  # export from the datasets themselves
    for bench in ("classeval", "humanevalplus"):
        loader = {"classeval": T.load_classeval, "humanevalplus": T.load_humanevalplus}[bench]
        lines = [json.dumps(dataclasses.asdict(t), sort_keys=True) for t in loader()]
        text = "\n".join(lines) + "\n"
        (out / f"{bench}.jsonl").write_text(text)
        print(bench, len(lines), hashlib.sha256(text.encode()).hexdigest()[:16])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
