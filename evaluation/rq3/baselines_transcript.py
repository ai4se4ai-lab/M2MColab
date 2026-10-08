"""Transcript-based failure attribution baselines (Who&When methods:
all-at-once, step-by-step, binary search), run with the same model on the
same failed runs rendered as transcripts with full inputs.

Also scores AutoM2M's trace-based attribution against the injected ground
truth, so that both are measured on identical runs.

    python -m evaluation.rq3.baselines_transcript --model qwen2.5-coder:7b
"""
from __future__ import annotations

import argparse
import json
import os
import re
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OLLAMA = os.environ.get("AM2M_OLLAMA", "http://127.0.0.1:11435")

CLASSES = """Fault classes:
- sampling: the agent's model produced a bad answer by chance; the same input could give a correct one
- footprint: the agent did not receive information it needed (its input lacks something)
- upstream: an earlier agent passed on a wrong value that this agent relied on
- specification: the agent's instruction or its check cannot be satisfied
- validator: the check itself is unreliable (gives different verdicts on the same answer)"""


# --------------------------------------------------------------------------
# rendering and ground truth
# --------------------------------------------------------------------------

def steps_of(row: dict) -> list[dict]:
    """One step per LLM call of the run phase, with its validator verdicts."""
    reg = row["registry"]
    owners = row["owners"]
    steps: list[dict] = []
    for e in row["transcript"]:
        if e["role"] == "binding":
            key = next((k for k, head in reg.items() if e["prompt"].startswith(head)), None)
            m = re.search(r"\[m\.name\]\n(\w+)\n", e["prompt"]) or re.search(r"\[m\.signature\]\ndef (\w+)\(", e["prompt"])
            steps.append({"agent": owners.get(key, "?"), "binding": key, "goal": m.group(1) if m else None,
                          "input": e["prompt"], "output": e["output"], "checks": []})
        elif e["role"] == "check" and steps:
            steps[-1]["checks"].append(("accepted" if e["ok"] else f"rejected: {e['reason']}")[:300])
    return steps


def render(steps: list[dict], upto: int | None = None, max_in: int = 1500, max_out: int = 900) -> str:
    out = []
    for i, s in enumerate(steps[: (upto + 1) if upto is not None else None]):
        what = f"{s['binding']} for method `{s['goal']}`" if s["goal"] else s["binding"]
        inp = s["input"] if len(s["input"]) <= max_in else s["input"][:max_in // 2] + "\n...\n" + s["input"][-max_in // 2:]
        o = s["output"][:max_out]
        checks = "; ".join(s["checks"]) or "(no check)"
        out.append(f"### Step {i} | agent: {s['agent']} | produces {what}\nINPUT:\n{inp}\nOUTPUT:\n{o}\nCHECK: {checks}")
    return "\n\n".join(out)


def truth(row: dict, steps: list[dict]) -> dict:
    f, method = row["fault"], row["method"]
    if f == "upstream":
        gt_steps = [i for i, s in enumerate(steps) if s["binding"] == "Method2Test.code" and s["goal"] == method]
        return {"agent": "Tester", "binding": "Method2Test.code", "goal": method, "steps": gt_steps, "class": "upstream"}
    if f == "sampling":
        gt_steps = [i for i, s in enumerate(steps) if s["binding"] == "Method2Impl.code" and s["goal"] == method]
        return {"agent": "Developer", "binding": "Method2Impl.code", "goal": method, "steps": gt_steps, "class": "sampling"}
    gt_steps = [i for i, s in enumerate(steps) if s["binding"] == "Method2Impl.code"]
    agent = "Developer" if f != "validator" else "(validator)"
    return {"agent": agent, "binding": "Method2Impl.code", "goal": None, "steps": gt_steps, "class": f}


def score_trace(row: dict, gt: dict) -> dict:
    a = row.get("attribution") or {}
    cls = a.get("fault_class")
    if cls == "upstream" and a.get("upstream"):
        agent, binding = a["upstream"].get("agent"), f"{a['upstream']['rule']}.{a['upstream']['binding']}"
        tk = a["upstream"].get("target_key") or ""
    else:
        agent, binding, tk = a.get("agent"), f"{a.get('rule')}.{a.get('binding')}", a.get("target_key") or ""
    if cls == "validator":
        agent = "(validator)"
    goal_ok = gt["goal"] is None or f"Method#{gt['goal']}" in tk
    return {"agent_ok": agent == gt["agent"], "binding_ok": binding == gt["binding"] and goal_ok,
            "class_ok": cls == gt["class"], "pred_class": cls, "calls": row.get("attribution_calls", 0)}


# --------------------------------------------------------------------------
# baselines
# --------------------------------------------------------------------------

def _ask(llm, prompt: str) -> dict:
    from evaluation.conditions.common import json_from

    out = llm.generate(prompt, temperature=0.0, format="json", max_tokens=400)
    return json_from(out) or {}


HEAD = ("A team of LLM agents (Tester writes unit tests per method; Developer implements each method, and its code "
        "is checked by running the documented examples and the Tester's tests) failed to complete its task. "
        "Each step below is one LLM call with its full input, its output and the automatic check of the output.\n\n")


def all_at_once(llm, steps) -> dict:
    p = (HEAD + render(steps) + "\n\n" + CLASSES +
         "\n\nWhich agent is responsible for the failure, at which step did the decisive mistake happen, and what is "
         "the fault class? Answer JSON {\"agent\": \"Tester\"|\"Developer\"|\"(validator)\", \"step\": int, \"class\": str}.")
    r = _ask(llm, p)
    return {"agent": r.get("agent"), "step": r.get("step"), "class": r.get("class"), "calls": 1}


def step_by_step(llm, steps) -> dict:
    calls = 0
    for i in range(len(steps)):
        p = (HEAD + render(steps, upto=i) + f"\n\nConsider only step {i}. Is step {i} the decisive mistake that makes "
             "the team fail? " + CLASSES + "\nAnswer JSON {\"decisive\": true|false, \"class\": str}.")
        r = _ask(llm, p)
        calls += 1
        if r.get("decisive") in (True, "true", "yes"):
            return {"agent": steps[i]["agent"], "step": i, "class": r.get("class"), "calls": calls}
    return {"agent": steps[-1]["agent"] if steps else None, "step": len(steps) - 1, "class": None, "calls": calls}


def binary_search(llm, steps) -> dict:
    lo, hi, calls = 0, len(steps) - 1, 0
    while lo < hi:
        mid = (lo + hi) // 2
        p = (HEAD + render(steps) + f"\n\nIs the decisive mistake in steps {lo}-{mid} or in steps {mid + 1}-{hi}? "
             "Answer JSON {\"half\": \"first\"|\"second\"}.")
        r = _ask(llm, p)
        calls += 1
        if str(r.get("half", "first")).lower().startswith("s"):
            lo = mid + 1
        else:
            hi = mid
    p = HEAD + render(steps) + f"\n\nThe decisive mistake is at step {lo}. " + CLASSES + "\nAnswer JSON {\"class\": str}."
    r = _ask(llm, p)
    calls += 1
    return {"agent": steps[lo]["agent"] if steps else None, "step": lo, "class": r.get("class"), "calls": calls}


METHODS = {"all_at_once": all_at_once, "step_by_step": step_by_step, "binary_search": binary_search}


def score_pred(pred: dict, gt: dict, steps) -> dict:
    step = pred.get("step")
    try:
        step = int(step)
    except (TypeError, ValueError):
        step = -1
    s = steps[step] if 0 <= step < len(steps) else None
    binding_ok = bool(s) and s["binding"] == gt["binding"] and (gt["goal"] is None or s["goal"] == gt["goal"])
    cls = str(pred.get("class") or "").lower().strip()
    return {"agent_ok": str(pred.get("agent") or "").strip() == gt["agent"], "binding_ok": binding_ok,
            "step_ok": step in gt["steps"], "class_ok": cls == gt["class"], "pred_class": cls, "calls": pred.get("calls")}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", required=True)
    ap.add_argument("--workers", type=int, default=4)
    a = ap.parse_args(argv)
    from agentm2m.llm.metered import MeteredBackend
    from agentm2m.llm.ollama_backend import OllamaBackend

    src = ROOT / "results" / "rq3" / f"inject__{a.model.replace(':', '_')}.jsonl"
    rows = [json.loads(l) for l in src.read_text().splitlines()]
    rows = [r for r in rows if r.get("manifested") and r.get("transcript")]
    out = ROOT / "results" / "rq3" / f"attribution__{a.model.replace(':', '_')}.jsonl"
    done = set()
    if out.exists():
        done = {(json.loads(l)["task"], json.loads(l)["fault"]) for l in out.read_text().splitlines()}

    def work(row):
        steps = steps_of(row)
        gt = truth(row, steps)
        llm = MeteredBackend(OllamaBackend(OLLAMA, a.model, seed=1, num_ctx=32768, timeout=900, max_tokens=400))
        res = {"task": row["task"], "fault": row["fault"], "n_steps": len(steps), "truth": gt,
               "trace": score_trace(row, gt)}
        for name, fn in METHODS.items():
            before = llm.totals()
            pred = fn(llm, steps)
            after = llm.totals()
            res[name] = {**score_pred(pred, gt, steps), "pred": pred,
                         "in_tokens": after["in_tokens"] - before["in_tokens"]}
        return res

    todo = [r for r in rows if (r["task"], r["fault"]) not in done]
    print(f"{len(todo)} runs to attribute", flush=True)
    with ThreadPoolExecutor(a.workers) as ex, out.open("a") as fh:
        for res in ex.map(work, todo):
            fh.write(json.dumps(res) + "\n")
            fh.flush()
            print(res["task"], res["fault"], "trace", res["trace"]["class_ok"], res["trace"]["agent_ok"],
                  {m: (res[m]["agent_ok"], res[m]["class_ok"]) for m in METHODS}, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
