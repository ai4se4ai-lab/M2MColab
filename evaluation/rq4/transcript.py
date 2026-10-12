"""RQ4: transcript-based failure attribution on the same injected runs.

The four methods of Who&When and TraceElephant receive each injected run as a
transcript with full inputs (one step per LLM call: agent, input, output and
the automatic check of the output), a judge model, and the same closed label
set of five fault classes:

  all_at_once     one prompt with the whole transcript
  step_by_step    one prompt per step, in order, until a step is judged decisive
  binary_search   halve the step range until one step remains, then classify
  agentic_replay  (TraceElephant-style) the judge inspects steps and may
                  re-run any step on its recorded input before it answers

Trace-based attribution of AutoM2M is scored on the same runs from the
exhaustive record (one replay, and the adaptive budget).

    python -m evaluation.rq4.transcript --source ref
"""
from __future__ import annotations

import argparse
import json
import re
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from evaluation import llms

ROOT = Path(__file__).resolve().parents[2]
CLASSES = """Fault classes (choose one):
- sampling: the agent's model produced a bad answer by chance; the same input could give a correct one
- footprint: the agent did not receive information it needed (its input lacks something)
- upstream: an earlier agent passed on a wrong value that this agent relied on
- specification: the agent's instruction or its check cannot be satisfied from its input
- validator: the check itself is unreliable (it gives different verdicts on the same answer)"""
LABELS = ("sampling", "footprint", "upstream", "specification", "validator")


# --------------------------------------------------------------------------
# steps and ground truth
# --------------------------------------------------------------------------

def steps_of(row: dict) -> list[dict]:
    """One step per LLM call, with the check verdicts that followed it: the
    clean run compactly (agent, value, output head, verdicts), then the run
    after the injection with full inputs."""
    heads = sorted(((k, v[:80]) for k, v in (row.get("registry") or {}).items()), key=lambda kv: -len(kv[1]))
    owners = row.get("owners") or {}
    steps: list[dict] = []
    for e in row.get("clean_summary") or []:
        key = next((k for k, h in heads if e["head"].startswith(h[:len(e["head"])]) or h.startswith(e["head"])), None)
        steps.append({"agent": owners.get(key, "?"), "binding": key, "method": e.get("method"),
                      "input": "(earlier step; input not repeated)", "output": e["output"], "checks": e.get("checks", [])})
    for e in row.get("transcript") or []:
        if e.get("role") == "binding":
            key = next((k for k, h in heads if e["prompt"].startswith(h)), None)
            m = re.search(r"def (\w+)\(", e["prompt"])
            steps.append({"agent": owners.get(key, "?"), "binding": key, "method": m.group(1) if m else None,
                          "input": e["prompt"], "output": e["output"], "checks": []})
        elif e.get("role") == "check" and steps:
            steps[-1]["checks"].append("accepted" if e.get("ok") else f"rejected: {str(e.get('reason'))[:200]}")
    return steps


def truth_steps(row: dict, steps: list[dict]) -> list[int]:
    t = row["truth"]
    return [i for i, s in enumerate(steps) if s["binding"] == t["binding"]
            and (not t.get("method") or s["method"] == t["method"])]


def render(steps: list[dict], upto: int | None = None, max_in: int = 1200, max_out: int = 700) -> str:
    out = []
    for i, s in enumerate(steps[: (upto + 1) if upto is not None else None]):
        inp = s["input"] if len(s["input"]) <= max_in else s["input"][:max_in // 2] + "\n...\n" + s["input"][-max_in // 2:]
        out.append(f"### Step {i} | agent: {s['agent']} | produces {s['binding']}"
                   + (f" for `{s['method']}`" if s["method"] else "")
                   + f"\nINPUT:\n{inp}\nOUTPUT:\n{s['output'][:max_out]}\nCHECK: {'; '.join(s['checks']) or '(none)'}")
    return "\n\n".join(out)


def overview(steps: list[dict]) -> str:
    return "\n".join(f"{i}: {s['agent']} -> {s['binding']}" + (f" ({s['method']})" if s["method"] else "")
                     + f" | {'; '.join(s['checks'])[:120] or 'no check'}" for i, s in enumerate(steps))


HEAD = ("A team of LLM agents failed its task. Each step below is one LLM call of one agent with its full input, its "
        "output and the automatic check of the output. Agents: {agents}; '(validator)' blames the check itself.\n\n")


def _ask(llm, prompt: str, max_tokens: int = 300) -> dict:
    from autom2m.loop import json_from

    try:
        return json_from(llm.generate(prompt, temperature=0.0, format="json", max_tokens=max_tokens)) or {}
    except Exception:  # noqa: BLE001
        return {}


def _head(steps):
    return HEAD.format(agents=", ".join(sorted({s["agent"] for s in steps})))


def all_at_once(llm, steps) -> dict:
    r = _ask(llm, _head(steps) + render(steps) + "\n\n" + CLASSES +
             "\n\nWhich agent is responsible, at which step is the decisive mistake, and what is the fault class? "
             "Answer JSON {\"agent\": str, \"step\": int, \"class\": str}.")
    return {"agent": r.get("agent"), "step": r.get("step"), "class": r.get("class"), "calls": 1}


def step_by_step(llm, steps) -> dict:
    calls = 0
    for i in range(len(steps)):
        r = _ask(llm, _head(steps) + render(steps, upto=i) + f"\n\nConsider step {i}. Is it the decisive mistake that "
                 "makes the team fail? " + CLASSES + "\nAnswer JSON {\"decisive\": true|false, \"class\": str}.")
        calls += 1
        if r.get("decisive") in (True, "true", "yes"):
            return {"agent": steps[i]["agent"], "step": i, "class": r.get("class"), "calls": calls}
    return {"agent": steps[-1]["agent"] if steps else None, "step": len(steps) - 1, "class": None, "calls": calls}


def binary_search(llm, steps) -> dict:
    lo, hi, calls = 0, len(steps) - 1, 0
    while lo < hi:
        mid = (lo + hi) // 2
        r = _ask(llm, _head(steps) + render(steps) + f"\n\nIs the decisive mistake in steps {lo}-{mid} or in steps "
                 f"{mid + 1}-{hi}? Answer JSON {{\"half\": \"first\"|\"second\"}}.")
        calls += 1
        if str(r.get("half", "first")).lower().startswith("s"):
            lo = mid + 1
        else:
            hi = mid
    r = _ask(llm, _head(steps) + render(steps) + f"\n\nThe decisive mistake is at step {lo}. " + CLASSES +
             "\nAnswer JSON {\"class\": str}.")
    return {"agent": steps[lo]["agent"] if steps else None, "step": lo, "class": r.get("class"), "calls": calls + 1}


def agentic_replay(llm, steps, subject, max_actions: int = 8) -> dict:
    """The judge sees an overview and acts: show a step in full, or replay it
    (re-run the step's exact input on the agent's model), then answers."""
    log, calls = [], 0
    for _ in range(max_actions):
        r = _ask(llm, _head(steps) + "OVERVIEW (step: agent -> value | check):\n" + overview(steps) + "\n\nYOUR NOTES:\n"
                 + ("\n".join(log) or "(none)") + "\n\n" + CLASSES +
                 "\n\nChoose ONE action. Inspect a step: {\"action\": \"show\", \"step\": i}. Re-run a step on its exact "
                 "input to see whether the agent can do better: {\"action\": \"replay\", \"step\": i}. Or answer: "
                 "{\"action\": \"answer\", \"agent\": str, \"step\": int, \"class\": str}.", max_tokens=200)
        calls += 1
        act = str(r.get("action", "answer"))
        try:
            i = int(r.get("step", -1))
        except (TypeError, ValueError):
            i = -1
        if act == "answer" or not 0 <= i < len(steps):
            return {"agent": r.get("agent"), "step": r.get("step"), "class": r.get("class"), "calls": calls}
        s = steps[i]
        if act == "show":
            log.append(f"step {i} input: {s['input'][-900:]}\nstep {i} output: {s['output'][:500]}\nstep {i} check: "
                       f"{'; '.join(s['checks'])}")
        else:
            try:
                out = subject.generate(s["input"], temperature=0.2)
            except Exception as exc:  # noqa: BLE001
                out = f"(error {exc})"
            calls += 1
            log.append(f"replay of step {i} gave: {out[:500]}")
    r = _ask(llm, _head(steps) + "OVERVIEW:\n" + overview(steps) + "\n\nNOTES:\n" + "\n".join(log) + "\n\n" + CLASSES +
             "\nAnswer JSON {\"agent\": str, \"step\": int, \"class\": str}.")
    return {"agent": r.get("agent"), "step": r.get("step"), "class": r.get("class"), "calls": calls + 1}


def score(pred: dict, row: dict, steps: list[dict]) -> dict:
    gt = row["truth"]
    gsteps = truth_steps(row, steps)
    try:
        step = int(pred.get("step"))
    except (TypeError, ValueError):
        step = -1
    cls = str(pred.get("class") or "").lower().strip()
    cls = next((c for c in LABELS if cls.startswith(c)), cls)
    return {"agent_ok": str(pred.get("agent") or "").strip() == gt["agent"], "step_ok": step in gsteps,
            "class_ok": cls == gt["class"], "pred_class": cls, "calls": pred.get("calls")}


def score_trace(row: dict) -> dict:
    gt = row["truth"]
    rep = row.get("report") or {}
    resp = row.get("responsible") or {}
    sym = row.get("symptom") or {}
    binding_ok = f"{resp.get('rule')}.{resp.get('binding')}" == gt["binding"] and \
        (not gt.get("target") or resp.get("target_key") == gt["target"])
    return {"symptom_ok": (rep.get("rule"), rep.get("binding"), rep.get("target_key")) ==
            (sym.get("rule"), sym.get("binding"), sym.get("target")),
            "agent_ok": resp.get("agent") == gt["agent"] or (gt["agent"] == "(validator)" and rep.get("fault_class") == "validator"),
            "binding_ok": binding_ok,
            "class_n1": row.get("class_n1") == gt["class"], "class_adaptive": row.get("class_n3") == gt["class"],
            "calls_n1": row.get("calls_n1"), "calls_adaptive": row.get("calls_n3")}


METHODS = {"all_at_once": all_at_once, "step_by_step": step_by_step, "binary_search": binary_search}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", choices=["ref", "builder"], default="ref")
    ap.add_argument("--model", default="qwen2.5-coder:7b")
    ap.add_argument("--workers", type=int, default=8)
    a = ap.parse_args(argv)
    src = ROOT / "results" / "rq4" / f"inject_{a.source}__{a.model.replace(':', '_')}.jsonl"
    rows = [json.loads(l) for l in src.read_text().splitlines() if l.strip()]
    rows = [r for r in rows if r.get("manifested") and r.get("transcript")]
    out = ROOT / "results" / "rq4" / f"transcript_{a.source}__{a.model.replace(':', '_')}.jsonl"
    done = set()
    if out.exists():
        done = {(json.loads(l)["task"], json.loads(l)["fault"]) for l in out.read_text().splitlines() if l.strip()}
    jspec = llms.judge()

    def work(row):
        steps = steps_of(row)
        judge = llms.make(jspec[0], jspec[1], max_tokens=400)
        subject = llms.make(a.model, 7, max_tokens=2048)
        res = {"task": row["task"], "fault": row["fault"], "team": row["team"], "n_steps": len(steps),
               "truth": row["truth"], "judge": llms.label(jspec), "trace": score_trace(row)}
        for name, fn in METHODS.items():
            before = judge.totals()
            pred = fn(judge, steps)
            res[name] = {**score(pred, row, steps), "pred": pred,
                         "in_tokens": judge.totals()["in_tokens"] - before["in_tokens"]}
        before = judge.totals()
        pred = agentic_replay(judge, steps, subject)
        res["agentic_replay"] = {**score(pred, row, steps), "pred": pred,
                                 "in_tokens": judge.totals()["in_tokens"] - before["in_tokens"]}
        return res

    todo = [r for r in rows if (r["task"], r["fault"]) not in done]
    print(f"{len(todo)} injected runs to attribute from transcripts", flush=True)
    with ThreadPoolExecutor(a.workers) as ex, out.open("a") as fh:
        for res in ex.map(work, todo):
            fh.write(json.dumps(res) + "\n")
            fh.flush()
            print(res["task"], res["fault"], "trace", res["trace"]["class_adaptive"],
                  {m: (res[m]["agent_ok"], res[m]["class_ok"]) for m in list(METHODS) + ["agentic_replay"]}, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
