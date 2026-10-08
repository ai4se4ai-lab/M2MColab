"""RQ1 (and RQ2 'composition-caused failures'): codebook coding of failed runs.

Two independent LLM coders (the two study models) label the decisive cause of
each failure with one code from the codebook; agreement is Cohen's kappa;
disagreements are adjudicated by the larger model given both labels.

Sources:
  whowhen   the 126 algorithm-generated Who&When failures (+ the expert annotation)
  ours      a stratified sample of failed team runs per (bench, model, condition)

    python -m evaluation.rq1.code_failures --source whowhen
    python -m evaluation.rq1.code_failures --source ours --per-cell 30
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import random
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OLLAMA = os.environ.get("AM2M_OLLAMA", "http://127.0.0.1:11435")
CODERS = ["qwen3.8:27b", "qwen2.5-coder:7b"]
CODES = ["D1", "D2", "D3", "D4", "D5", "reasoning", "tool", "other"]

CODEBOOK = """Codebook: choose the ONE code for the DECISIVE cause of the failure (what, if fixed, would most
likely have prevented it).
D1 coverage gap: part of the task (a method, a requirement, a plan step) is owned by no agent, or a requirement is
   never checked by anyone.
D2 hand-off mismatch: what one agent passed on was not what the next agent needed (missing details, wrong form,
   misread reference), or an agent built on a wrong intermediate result passed to it as fact.
D3 ownership conflict: two agents worked on/overwrote the same artefact, or nobody took responsibility for it.
D4 unverifiable completion: the team declared success (TERMINATE, "verified", status done) although nothing
   checked the result, or a check was ignored.
D5 capability mismatch: work went to an agent lacking the tool or skill it needed.
reasoning: one agent's own reasoning/coding error with adequate inputs (wrong algorithm, bug, misread spec).
tool: execution environment or tool failure (timeouts, missing library, sandbox).
other: none of the above.
Composition defects are D1-D5."""


def _llm(model: str):
    from agentm2m.llm.metered import MeteredBackend
    from agentm2m.llm.ollama_backend import OllamaBackend

    return MeteredBackend(OllamaBackend(OLLAMA, model, seed=7, num_ctx=32768, timeout=900, max_tokens=300))


def _clip(text: str, n: int) -> str:
    return text if len(text) <= n else text[: n // 2] + "\n...[clipped]...\n" + text[-n // 2:]


# --------------------------------------------------------------------------
# items
# --------------------------------------------------------------------------

def whowhen_items() -> list[dict]:
    root = ROOT / "data" / "Agents_Failure_Attribution" / "Who&When" / "Algorithm-Generated"
    items = []
    for f in sorted(root.glob("*.json"), key=lambda p: int(p.stem)):
        d = json.loads(f.read_text())
        roles = "\n".join(f"- {k}: {_clip(v or '', 500)}" for k, v in (d.get("system_prompt") or {}).items())
        hist = "\n\n".join(f"[{i}] {m.get('name')}: {_clip(m.get('content') or '', 700)}" for i, m in enumerate(d["history"]))
        text = (f"TASK: {d['question'][:800]}\nCORRECT ANSWER: {d.get('ground_truth')}\n\nTEAM SPECIFICATION (generated roles):\n"
                f"{roles}\n\nRUN:\n{_clip(hist, 9000)}\n\nEXPERT ANNOTATION: the decisive mistake was made by "
                f"{d.get('mistake_agent')} at step {d.get('mistake_step')}: {d.get('mistake_reason')}")
        items.append({"id": f"ww/{f.stem}", "source": "whowhen", "condition": "captainagent", "text": text})
    return items


def our_items(per_cell: int, seed: int = 0) -> list[dict]:
    rng = random.Random(seed)
    items = []
    for bench_dir in sorted(glob.glob(str(ROOT / "results" / "runs" / "*" / "*"))):
        bench, model = Path(bench_dir).parent.name, Path(bench_dir).name
        for cond in ["free", "critic", "schema", "typed_unchecked", "autom2m", "typed_ref"]:
            files = sorted(glob.glob(f"{bench_dir}/{cond}/*__s1.json"))
            failed = []
            for f in files:
                d = json.loads(Path(f).read_text())
                if not d.get("success"):
                    failed.append((f, d))
            rng.shuffle(failed)
            for f, d in failed[:per_cell]:
                items.append({"id": f"{bench}/{model}/{cond}/{Path(f).stem}", "source": "ours", "bench": bench,
                              "model": model, "condition": cond, "text": render_run(d)})
    return items


def render_run(d: dict) -> str:
    det = d.get("detail") or {}
    sc = d.get("score") or {}
    outcome = f"HIDDEN TESTS: {sc.get('tests_passed')}/{sc.get('tests_run')} passed. {str(sc.get('error') or '')[:300]}"
    if "transcript" in det:
        team = json.dumps(det.get("team"), indent=1)
        tr = "\n\n".join(f"[{i}] {m['agent']}: {_clip(m['content'], 900)}" for i, m in enumerate(det["transcript"]))
        return f"TEAM SPECIFICATION:\n{_clip(team, 2500)}\n\nRUN:\n{_clip(tr, 9000)}\n\n{outcome}"
    team = json.dumps(det.get("final_team") or det.get("first_team"), separators=(",", ":"))
    diags = det.get("diagnostics") or []
    faults = det.get("fault_reports") or []
    lines = [f"TYPED TEAM (JSON):\n{_clip(team, 3500)}",
             f"ADMISSION: status={det.get('status')} rounds={det.get('admission_rounds')} last diagnostics={diags[-1] if diags else []}",
             f"ENGINE: acceptance predicate holds={det.get('phi')}; accepted deliverables={det.get('accepted')}",
             "FAULT REPORTS:\n" + "\n".join(f"- {f.get('fault_class')} at {f.get('rule')}.{f.get('binding')} "
                                            f"(agent {f.get('agent')}): {str(f.get('reason'))[:250]}" for f in faults[:6]),
             f"REPAIRS: {json.dumps(det.get('repairs'))[:800]}", outcome]
    return "\n\n".join(lines)


# --------------------------------------------------------------------------
# coding
# --------------------------------------------------------------------------

def code_one(llm, item: dict) -> dict:
    from evaluation.conditions.common import json_from

    prompt = (f"You analyse why a team of LLM agents failed.\n{CODEBOOK}\n\n{item['text']}\n\n"
              "Answer JSON {\"code\": one of " + json.dumps(CODES) + ", \"why\": short reason}.")
    try:
        r = json_from(llm.generate(prompt, temperature=0.0, format="json")) or {}
    except Exception as exc:  # noqa: BLE001
        r = {"code": "other", "why": f"coder error {exc}"}
    code = str(r.get("code", "other")).strip()
    code = next((c for c in CODES if c.lower() == code.lower()), "other")
    return {"code": code, "why": str(r.get("why", ""))[:300]}


def adjudicate(llm, item: dict, a: dict, b: dict) -> dict:
    from evaluation.conditions.common import json_from

    prompt = (f"Two analysts disagree on the decisive cause of this failure.\n{CODEBOOK}\n\n{item['text']}\n\n"
              f"Analyst A: {a['code']} ({a['why']})\nAnalyst B: {b['code']} ({b['why']})\n"
              "Decide. Answer JSON {\"code\": one of " + json.dumps(CODES) + ", \"why\": short reason}.")
    r = json_from(llm.generate(prompt, temperature=0.0, format="json")) or {}
    code = next((c for c in CODES if c.lower() == str(r.get("code", "")).lower()), a["code"])
    return {"code": code, "why": str(r.get("why", ""))[:300]}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", choices=["whowhen", "ours"], required=True)
    ap.add_argument("--per-cell", type=int, default=30)
    ap.add_argument("--workers", type=int, default=3)
    a = ap.parse_args(argv)
    items = whowhen_items() if a.source == "whowhen" else our_items(a.per_cell)
    out = ROOT / "results" / "rq1" / f"codes_{a.source}.jsonl"
    out.parent.mkdir(parents=True, exist_ok=True)
    done = set()
    if out.exists():
        done = {json.loads(l)["id"] for l in out.read_text().splitlines()}
    todo = [it for it in items if it["id"] not in done]
    print(f"{len(todo)} items to code", flush=True)
    big, small = _llm(CODERS[0]), _llm(CODERS[1])

    def work(it):
        ca, cb = code_one(big, it), code_one(small, it)
        final = ca if ca["code"] == cb["code"] else adjudicate(big, it, ca, cb)
        meta = {k: v for k, v in it.items() if k != "text"}
        return {**meta, "coder_27b": ca, "coder_7b": cb, "final": final["code"], "agree": ca["code"] == cb["code"]}

    with ThreadPoolExecutor(a.workers) as ex, out.open("a") as fh:
        for r in ex.map(work, todo):
            fh.write(json.dumps(r) + "\n")
            fh.flush()
            print(r["id"], r["coder_27b"]["code"], r["coder_7b"]["code"], "->", r["final"], flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
