"""hpc/scripts/check_sizing.py — load models the way a pipeline stage does and report GPU residency.

    check_sizing.py --ctx 32768 --models qwen2.5-coder:32b devstral:24b

Run against the Ollama server named by AM2M_OLLAMA (the server's OLLAMA_NUM_PARALLEL / OLLAMA_MAX_LOADED_MODELS
are what the stage uses). Loads each model with num_ctx=--ctx and keep_alive, then reads /api/ps. Exit code 1 if
any requested model is missing from GPU memory (evicted) or is not 100% on the GPU (CPU offload = very slow).
"""
from __future__ import annotations

import argparse
import os
import sys
import time

import requests

OLLAMA = os.environ.get("AM2M_OLLAMA", "http://127.0.0.1:11434")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ctx", type=int, required=True)
    ap.add_argument("--models", nargs="+", required=True)
    a = ap.parse_args()
    bad = False
    for tag in a.models:
        t0 = time.time()
        r = requests.post(f"{OLLAMA}/api/generate", timeout=1800,
                          json={"model": tag, "prompt": "", "keep_alive": "24h", "options": {"num_ctx": a.ctx}})
        print(f"  load {tag:24s} http {r.status_code}  {time.time() - t0:5.0f}s", flush=True)
        bad |= not r.ok
    ps = {x["name"]: x for x in requests.get(f"{OLLAMA}/api/ps", timeout=30).json().get("models", [])}
    total = 0.0
    for tag in a.models:
        x = ps.get(tag)
        if not x:
            print(f"  RESIDENT {tag:24s} NOT LOADED (evicted by a later model: the set does not fit together)")
            bad = True
            continue
        size, vram = x.get("size", 0), x.get("size_vram", 0)
        share = vram / size if size else 0
        total += vram / 2**30
        flag = "" if share >= 0.999 else "   <-- NOT FULLY ON GPU (CPU offload)"
        bad |= share < 0.999
        print(f"  RESIDENT {tag:24s} {size / 2**30:6.1f} GiB total, {vram / 2**30:6.1f} GiB on GPU = {100 * share:3.0f}%"
              f"   ctx={x.get('context_length', '?')}{flag}")
    print(f"  GPU memory used by these models: {total:.1f} GiB of ~80")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
