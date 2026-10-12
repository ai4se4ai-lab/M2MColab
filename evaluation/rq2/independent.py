"""RQ2: independently seeded defects, a clean set, and LLM critics (Fig. 5).

To avoid evaluating the checker only on defects designed for it, an LLM that
sees only the plain-language definitions of D1-D5 (Table 2), never W1-W6,
seeds one defect into each admitted builder-generated team. Mutants equal to
their original (after normalisation) are removed as equivalent. The clean set
is built without the checker: hand-written typed teams for ClassEval classes
(clean_teams.py) and builder teams of the unchecked condition (Typed-NC) that
reached phi and passed the hidden tests. The checker (two- and one-sided W4)
and the LLM critics receive the same JSON specification and definitions.

    python -m evaluation.rq2.independent seed --per-class 60
    python -m evaluation.rq2.independent clean
    python -m evaluation.rq2.independent critics
"""
from __future__ import annotations

import argparse
import copy
import glob
import json
import random
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from autom2m.checker import check
from autom2m.lift import normalize
from evaluation import llms

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "results" / "rq2"
DEFECTS = {
    "D1": "coverage gap: part of the task is owned by no agent, or a goal is never checked against anything that read it",
    "D2": "hand-off mismatch: what a producer emits is not what its consumer needs, in content, reference or form",
    "D3": "ownership conflict: two agents act on the same artefact, or none knows it is theirs",
    "D4": "unverifiable completion: 'done' is a claim in the conversation, not a checked property",
    "D5": "capability mismatch: work is routed to an agent that lacks the needed tool or skill",
}


def _strip_goal(t: dict) -> dict:
    t = copy.deepcopy(t)
    if isinstance(t.get("views"), dict):
        t["views"].pop("Goal", None)
    return t


def _key(t: dict) -> str:
    return json.dumps(normalize(t), sort_keys=True)


def builder_teams(admitted_only: bool = True, condition: str = "autom2m") -> list[dict]:
    """Distinct builder-generated teams from the runs (final teams)."""
    out, seen = [], set()
    for f in sorted(glob.glob(str(ROOT / "results" / "runs" / "*" / "*" / condition / "*.json"))):
        d = json.loads(Path(f).read_text())
        det = d.get("detail") or {}
        t = det.get("final_team")
        if not isinstance(t, dict) or (admitted_only and det.get("admitted_round") is None):
            continue
        k = _key(t)
        if k in seen:
            continue
        seen.add(k)
        out.append({"source": f"{d['bench']}/{d['task']}/s{d['seed']}", "team": _strip_goal(t), "run": d})
    return out


# --------------------------------------------------------------------------
# seeding
# --------------------------------------------------------------------------

SEED_PROMPT = """Below is the specification of a team of LLM agents (JSON). Introduce exactly ONE realistic defect of
the following kind, as a careless team builder might:
{defect}
Change as little as possible and keep the JSON format. Variant #{variant}.

TEAM:
{team}

Output JSON {{"team": <the modified team>, "description": <one sentence: what you changed>}}."""


def seed(per_class: int, workers: int) -> None:
    teams = builder_teams()
    rng = random.Random(0)
    jobs = []
    for d in DEFECTS:
        pool = list(range(len(teams)))
        rng.shuffle(pool)
        for i in range(per_class):
            if not pool:
                break
            jobs.append((d, teams[pool[i % len(pool)]], i + 1))
    spec = llms.seeder()
    print(f"{len(jobs)} seeding jobs over {len(teams)} admitted builder teams (seeder {llms.label(spec)})", flush=True)

    def work(job):
        from autom2m.loop import json_from

        d, item, variant = job
        llm = llms.make(spec[0], spec[1] + variant, max_tokens=4096)
        prompt = SEED_PROMPT.format(defect=f"{d} {DEFECTS[d]}", variant=variant, team=json.dumps(item["team"], indent=1))
        try:
            r = json_from(llm.generate(prompt, temperature=0.8, format="json")) or {}
        except Exception as exc:  # noqa: BLE001
            return {"defect": d, "origin": item["source"], "error": str(exc)[:200]}
        mt = r.get("team") if isinstance(r.get("team"), dict) else None
        if mt is None:
            return {"defect": d, "origin": item["source"], "error": "no team in the answer"}
        return {"defect": d, "origin": item["source"], "variant": variant, "description": str(r.get("description", ""))[:400],
                "equivalent": _key(mt) == _key(item["team"]), "mutant": _strip_goal(mt)}

    out = OUT / "seeded.jsonl"
    OUT.mkdir(parents=True, exist_ok=True)
    with ThreadPoolExecutor(workers) as ex, out.open("w") as fh:
        for r in ex.map(work, jobs):
            fh.write(json.dumps(r) + "\n")
            fh.flush()
            print(r["defect"], r.get("origin"), "equivalent" if r.get("equivalent") else "", r.get("error", ""), flush=True)


# --------------------------------------------------------------------------
# the clean set
# --------------------------------------------------------------------------

def clean() -> None:
    from evaluation.rq2.clean_teams import hand_written

    rows = [{"kind": "hand", "source": name, "team": t} for name, t in hand_written()]
    ok = []
    for item in builder_teams(admitted_only=False, condition="typed_nc"):
        run = item["run"]
        if run.get("success") and (run.get("detail") or {}).get("phi"):
            ok.append({"kind": "builder", "source": item["source"], "team": item["team"]})
    random.Random(1).shuffle(ok)
    rows += ok[:30]
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "clean.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    print(f"clean set: {sum(r['kind'] == 'hand' for r in rows)} hand-written, {sum(r['kind'] == 'builder' for r in rows)} builder teams")


# --------------------------------------------------------------------------
# checker and critics
# --------------------------------------------------------------------------

CRITIC = """You review the specification of a team of LLM agents (JSON) BEFORE it runs. Look for composition defects:
{defs}

SPECIFICATION:
{spec}

Output only JSON {{"defects": [{{"class": "D1".."D5", "where": text}}]}} (an empty list if the team has none)."""


def critics(workers: int) -> None:
    from autom2m.loop import json_from

    items = []
    for l in (OUT / "seeded.jsonl").read_text().splitlines():
        r = json.loads(l)
        if r.get("mutant") and not r.get("equivalent"):
            items.append({"kind": "seeded", "defect": r["defect"], "source": r["origin"], "team": r["mutant"]})
    for l in (OUT / "clean.jsonl").read_text().splitlines():
        r = json.loads(l)
        items.append({"kind": "clean", "defect": None, "source": f"{r['kind']}:{r['source']}", "team": r["team"]})
    defs = "\n".join(f"{k} {v}" for k, v in DEFECTS.items())
    specs = llms.critics()

    def work(it):
        res = {k: v for k, v in it.items() if k != "team"}
        for w4 in ("two-sided", "one-sided", "path-only"):
            c = check(normalize(copy.deepcopy(it["team"])), w4=w4)
            res[f"checker_{w4}"] = not c.admitted
            if w4 == "two-sided":
                res["conds"] = sorted(c.conds())
        for spec in specs:
            llm = llms.make(spec[0], spec[1], max_tokens=600)
            try:
                r = json_from(llm.generate(CRITIC.format(defs=defs, spec=json.dumps(it["team"], indent=1)),
                                           temperature=0.2, format="json")) or {}
            except Exception as exc:  # noqa: BLE001
                r = {"error": str(exc)}
            ds = r.get("defects") if isinstance(r.get("defects"), list) else []
            classes = sorted({str(d.get("class", ""))[:2] for d in ds if isinstance(d, dict)})
            res[f"critic:{llms.label(spec)}"] = {"flagged": bool(ds), "classes": classes}
        return res

    out = OUT / "independent.jsonl"
    with ThreadPoolExecutor(workers) as ex, out.open("w") as fh:
        for res in ex.map(work, items):
            fh.write(json.dumps(res) + "\n")
            fh.flush()
    print("wrote", out, len(items), "items")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["seed", "clean", "critics"])
    ap.add_argument("--per-class", type=int, default=60)
    ap.add_argument("--workers", type=int, default=8)
    a = ap.parse_args(argv)
    if a.cmd == "seed":
        seed(a.per_class, a.workers)
    elif a.cmd == "clean":
        clean()
    else:
        critics(a.workers)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
