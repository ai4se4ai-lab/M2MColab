"""RQ2 static: (1) independent mutants -- composition defects seeded by an LLM
that sees only the plain-language defect classes, never the W1-W6 rules;
(2) LLM-critic detection of the same mutants (and of the operator mutants)
rendered as prose, versus the checker.

    python -m evaluation.rq2.independent generate --model qwen3.8:27b
    python -m evaluation.rq2.independent critic --model qwen2.5-coder:7b
"""
from __future__ import annotations

import argparse
import copy
import json
import os
import random
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from agentm2m.auto.checker import check
from agentm2m.auto.repair import normalize
from agentm2m.auto.vlib import VLIB

ROOT = Path(__file__).resolve().parents[2]
OLLAMA = os.environ.get("AM2M_OLLAMA", "http://127.0.0.1:11435")
TEAMS = ["devteam_admitted_g2", "chakin_repaired_g2", "classeval_reference"]
DEFECTS = {
    "D1": "coverage gap: part of the task is owned by no agent, or a requirement is never checked",
    "D2": "hand-off mismatch: what one agent produces is not what the next agent needs (missing information, wrong form, dangling reference)",
    "D3": "ownership conflict: two agents write the same artefact, or the same kind of object is produced twice",
    "D4": "unverifiable completion: 'done' is a claim by an agent, or completion is not fully decided by checks",
    "D5": "capability mismatch: work is given to an agent that lacks a tool it needs",
}


def _llm(model, seed=1, max_tokens=4000):
    from agentm2m.llm.metered import MeteredBackend
    from agentm2m.llm.ollama_backend import OllamaBackend

    return MeteredBackend(OllamaBackend(OLLAMA, model, seed=seed, num_ctx=32768, timeout=900, max_tokens=max_tokens))


def load_team(name: str) -> dict:
    t = json.loads((ROOT / "teams" / f"{name}.json").read_text())
    return normalize(t) if t.get("goal_view") == "Goal" else t


# --------------------------------------------------------------------------
# prose rendering (what an LLM critic reviews)
# --------------------------------------------------------------------------

def render_prose(t: dict) -> str:
    lines = []
    for a in t.get("agents", []):
        writes = ", ".join(t.get("writes", {}).get(a["name"], [])) or "nothing"
        tools = ", ".join(a.get("tools", [])) or "no tools"
        lines.append(f"Agent {a['name']} ({tools}); writes: {writes}. Role: {a.get('role', '')}")
    lines.append(f"The task is given as the {t.get('goal_view')} view.")
    for v, spec in t.get("views", {}).items():
        for c, cs in (spec.get("classes") or {}).items():
            attrs = ", ".join(cs.get("attributes", {}) or {})
            refs = ", ".join(f"{r}->{(rs if isinstance(rs, str) else rs.get('type'))}" for r, rs in (cs.get("references") or {}).items())
            lines.append(f"Form {v}.{c}: fields {attrs}" + (f"; links {refs}" if refs else ""))
    for h in t.get("handoffs", []):
        for r in h.get("rules", []):
            src = " and ".join(f"each {s['type'].replace('!', '.')} ({s['var']})" for s in r.get("from", []))
            g = f" where {r['guard']}" if r.get("guard") else ""
            lines.append(f"Hand-off {h['name']} ({', '.join(h.get('sources', []))} -> {h.get('target')}): for {src}{g}, "
                         f"create a {r['to']['type'].replace('!', '.')}.")
            for f, e in (r.get("bind") or {}).items():
                lines.append(f"  - {f} is copied from {e}")
            for b in r.get("llm", []):
                vs = ", ".join(f"{v['id']}({', '.join(f'{k}={x}' for k, x in (v.get('args') or {}).items())})"
                               for v in (b.get("validator") or []))
                lines.append(f"  - {b['feature']} is written by an LLM ('{b.get('prompt', '')}') that sees "
                             f"{', '.join(b.get('footprint', []))}; it is accepted only if it passes {vs or 'no check'}")
    lines.append("Goals: " + "; ".join(f"every {g['class']} ({g.get('scope', 'all')}) must be {g.get('kind', 'checked')}"
                                       for g in t.get("goal", [])))
    if t.get("deliverable"):
        d = t["deliverable"]
        lines.append(f"Deliverable: {d['view']}.{d['class']}.{d['feature']} for each {d.get('for')}")
    lines.append("The team is done when: " + ", ".join(t.get("done", [])))
    lines.append("Validator library: " + "; ".join(f"{s.id} ({s.strength}, tools {list(s.tools)})" for s in VLIB.values() if s.offered))
    return "\n".join(lines)


# --------------------------------------------------------------------------
# (1) independent mutants
# --------------------------------------------------------------------------

def generate(model: str, per_cell: int) -> None:
    from evaluation.conditions.common import json_from

    out = ROOT / "results" / "rq2" / "independent_mutants.jsonl"
    rows = []
    jobs = [(tn, d, s) for tn in TEAMS for d in DEFECTS for s in range(1, per_cell + 1)]

    def work(job):
        tn, d, s = job
        team = load_team(tn)
        prompt = (f"Below is the specification of a team of LLM agents (JSON). Introduce exactly ONE realistic defect of "
                  f"this kind, as a careless team builder might:\n{d} {DEFECTS[d]}\n"
                  "Change as little as possible and keep the JSON format. Variant #" + str(s) + ".\n\nTEAM:\n"
                  + json.dumps(team, indent=1) + "\n\nOutput JSON {\"team\": <the modified team>, \"description\": "
                  "<one sentence: what you changed>}.")
        llm = _llm(model, seed=s)
        try:
            r = json_from(llm.generate(prompt, temperature=0.8, format="json", max_tokens=6000)) or {}
        except Exception as exc:  # noqa: BLE001
            return {"team": tn, "defect": d, "seed": s, "error": str(exc)}
        mt = r.get("team") if isinstance(r.get("team"), dict) else None
        if mt is None:
            return {"team": tn, "defect": d, "seed": s, "error": "no team in answer"}
        same = json.dumps(mt, sort_keys=True) == json.dumps(team, sort_keys=True)
        ra, rn = check(copy.deepcopy(mt)), check(copy.deepcopy(mt), anchored=False)
        return {"team": tn, "defect": d, "seed": s, "description": str(r.get("description", ""))[:400],
                "unchanged": same, "detected": not ra.admitted, "naive_detected": not rn.admitted,
                "conds": sorted(ra.conds()), "msgs": [str(x) for x in ra.diagnostics][:6], "mutant": mt}

    with ThreadPoolExecutor(2) as ex:
        for res in ex.map(work, jobs):
            rows.append(res)
            print(res["team"], res["defect"], res.get("seed"), "unchanged" if res.get("unchanged") else "",
                  res.get("detected"), res.get("conds"), res.get("error", ""), flush=True)
    out.write_text("\n".join(json.dumps(r) for r in rows) + "\n")


# --------------------------------------------------------------------------
# (2) critic detection
# --------------------------------------------------------------------------

CRITIC = """You review the specification of a team of LLM agents BEFORE it runs. Look for composition defects:
{defs}
SPECIFICATION:
{spec}

Output only JSON {{"defects": [{{"class": "D1".."D5", "where": text}}]}} (an empty list if the team has none)."""


def critic(model: str, repeats_clean: int = 5) -> None:
    from evaluation.conditions.common import json_from
    from evaluation.rq2.mutate import OPERATORS, mutants

    items = []
    for tn in ["devteam_admitted_g2", "chakin_repaired_g2"]:
        team = json.loads((ROOT / "teams" / f"{tn}.json").read_text())
        for s in range(repeats_clean):
            items.append({"kind": "clean", "team": tn, "defect": None, "seed": s + 1, "json": team})
        for op, site, m in mutants(team):
            items.append({"kind": "operator", "team": tn, "defect": OPERATORS[op][0], "op": op, "site": site, "seed": 1, "json": m})
    ref = load_team("classeval_reference")
    for s in range(repeats_clean):
        items.append({"kind": "clean", "team": "classeval_reference", "defect": None, "seed": s + 1, "json": ref})
    p = ROOT / "results" / "rq2" / "independent_mutants.jsonl"
    if p.exists():
        for l in p.read_text().splitlines():
            r = json.loads(l)
            if r.get("mutant") and not r.get("unchanged"):
                items.append({"kind": "independent", "team": r["team"], "defect": r["defect"], "seed": 1, "json": r["mutant"],
                              "checker_detected": r["detected"]})
    defs = "\n".join(f"{k} {v}" for k, v in DEFECTS.items())

    def work(it):
        llm = _llm(model, seed=it["seed"], max_tokens=600)
        try:
            r = json_from(llm.generate(CRITIC.format(defs=defs, spec=render_prose(it["json"])), temperature=0.2, format="json")) or {}
        except Exception as exc:  # noqa: BLE001
            r = {"error": str(exc)}
        ds = r.get("defects") if isinstance(r.get("defects"), list) else []
        classes = sorted({str(d.get("class", ""))[:2] for d in ds if isinstance(d, dict)})
        res = {k: v for k, v in it.items() if k != "json"}
        res.update(critic_flagged=bool(ds), critic_classes=classes, critic_correct_class=(it["defect"] in classes) if it["defect"] else None,
                   checker=not check(copy.deepcopy(it["json"])).admitted)
        return res

    out = ROOT / "results" / "rq2" / f"critic__{model.replace(':', '_')}.jsonl"
    with ThreadPoolExecutor(4) as ex, out.open("w") as fh:
        for res in ex.map(work, items):
            fh.write(json.dumps(res) + "\n")
            fh.flush()
    print("wrote", out)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["generate", "critic"])
    ap.add_argument("--model", required=True)
    ap.add_argument("--per-cell", type=int, default=4)
    a = ap.parse_args(argv)
    random.seed(0)
    if a.cmd == "generate":
        generate(a.model, a.per_cell)
    else:
        critic(a.model)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
