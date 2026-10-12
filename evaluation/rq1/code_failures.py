"""RQ1: how often is the decisive cause of a failure a composition defect?

Sources (paper Sec. 4.2, coding protocol):
  ww_auto   the 126 failures of CaptainAgent-built teams in Who&When (GAIA, AssistantBench)
  ww_hand   the 58 failures of the hand-crafted Magentic-One in Who&When
  ours      failures of our Free and Critic runs, sampled at random, stratified by model and benchmark

    python -m evaluation.rq1.code_failures --source whowhen
    python -m evaluation.rq1.code_failures --source ours --n 400

Output: results/rq1/codes_<source>.jsonl (both coders, adjudicated final code,
secondary code). The analysis computes shares with bootstrap CIs and kappa.
"""
from __future__ import annotations

import argparse
import glob
import json
import random
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from evaluation import llms
from evaluation.coding import _clip, code_item, render_run

ROOT = Path(__file__).resolve().parents[2]
WW = ROOT / "data" / "Agents_Failure_Attribution" / "Who&When"
RESULTS = ROOT / "results"


def whowhen_items() -> list[dict]:
    items = []
    for f in sorted((WW / "Algorithm-Generated").glob("*.json"), key=lambda p: int(p.stem)):
        d = json.loads(f.read_text())
        roles = "\n".join(f"- {k}: {_clip(v or '', 500)}" for k, v in (d.get("system_prompt") or {}).items())
        hist = "\n\n".join(f"[{i}] {m.get('name')}: {_clip(m.get('content') or '', 700)}" for i, m in enumerate(d["history"]))
        text = (f"TASK: {_clip(d['question'], 800)}\nCORRECT ANSWER: {d.get('ground_truth')}\n\n"
                f"TEAM SPECIFICATION (generated roles):\n{roles}\n\nRUN:\n{_clip(hist, 9000)}")
        ann = f"agent {d.get('mistake_agent')}, step {d.get('mistake_step')}: {d.get('mistake_reason')}"
        items.append({"id": f"ww_auto/{f.stem}", "source": "ww_auto", "text": text, "annotation": ann})
    for f in sorted((WW / "Hand-Crafted").glob("*.json"), key=lambda p: int(p.stem)):
        d = json.loads(f.read_text())
        hist = "\n\n".join(f"[{i}] {m.get('role')}: {_clip(m.get('content') or '', 700)}" for i, m in enumerate(d["history"]))
        text = (f"TASK: {_clip(d['question'], 800)}\nCORRECT ANSWER: {d.get('ground_truth')}\n\n"
                f"TEAM SPECIFICATION: the hand-crafted Magentic-One team (Orchestrator, WebSurfer, FileSurfer, Coder, "
                f"ComputerTerminal).\n\nRUN:\n{_clip(hist, 9000)}")
        ann = f"agent {d.get('mistake_agent')}, step {d.get('mistake_step')}: {d.get('mistake_reason')}"
        items.append({"id": f"ww_hand/{f.stem}", "source": "ww_hand", "text": text, "annotation": ann})
    return items


def our_items(n: int, seed: int = 0) -> list[dict]:
    """Failures of Free and Critic, stratified by (model, benchmark)."""
    rng = random.Random(seed)
    strata: dict = defaultdict(list)
    for f in sorted(glob.glob(str(RESULTS / "runs" / "*" / "*" / "*" / "*.json"))):
        p = Path(f)
        cond, model, bench = p.parent.name, p.parent.parent.name, p.parent.parent.parent.name
        if cond not in ("free", "critic"):
            continue
        d = json.loads(p.read_text())
        if d.get("success") or d.get("status") == "error":
            continue
        strata[(model, bench)].append((f, d, cond))
    items = []
    if not strata:
        return items
    per = max(1, n // len(strata))
    for (model, bench), rows in sorted(strata.items()):
        rng.shuffle(rows)
        for f, d, cond in rows[:per]:
            items.append({"id": f"ours/{bench}/{model}/{cond}/{Path(f).stem}", "source": "ours", "bench": bench,
                          "model": model, "condition": cond, "text": render_run(d)})
    return items


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", choices=["whowhen", "ours"], required=True)
    ap.add_argument("--n", type=int, default=400)
    ap.add_argument("--workers", type=int, default=8)
    a = ap.parse_args(argv)
    items = whowhen_items() if a.source == "whowhen" else our_items(a.n)
    out = RESULTS / "rq1" / f"codes_{a.source}.jsonl"
    out.parent.mkdir(parents=True, exist_ok=True)
    done = set()
    if out.exists():
        done = {json.loads(l)["id"] for l in out.read_text().splitlines() if l.strip()}
    todo = [it for it in items if it["id"] not in done]
    specs = llms.coders()
    print(f"{len(todo)} items to code with coders {[llms.label(s) for s in specs]}", flush=True)

    def work(it):
        coders = [llms.make(m, s, max_tokens=256) for m, s in specs[:2]]
        res = code_item(coders, it["text"], annotation=it.get("annotation", ""))
        meta = {k: v for k, v in it.items() if k not in ("text", "annotation")}
        return {**meta, **res, "coders": [llms.label(s) for s in specs[:2]]}

    with ThreadPoolExecutor(a.workers) as ex, out.open("a") as fh:
        for r in ex.map(work, todo):
            fh.write(json.dumps(r) + "\n")
            fh.flush()
            print(r["id"], r["coder_a"]["code"], r["coder_b"]["code"], "->", r["final"], flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
