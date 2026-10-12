"""RQ4 on natural failures: the responsible agent and binding of failed
AutoM2M runs, labelled by the coders from the run's transcript, against the
binding that trace lookup (and the first attribution) named.

    python -m evaluation.rq4.natural --n 150
"""
from __future__ import annotations

import argparse
import glob
import json
import random
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from evaluation import llms
from evaluation.coding import render_run

ROOT = Path(__file__).resolve().parents[2]

PROMPT = """A typed team of LLM agents failed its task. Every value an agent produced is one message below, with the
check verdict. Decide which agent and which value (the binding, written Rule.feature) is RESPONSIBLE for the failure:
the value that, had it been right, would most likely have let the team succeed.

{run}

Answer JSON {{"agent": str, "binding": "Rule.feature"}}."""


def items(n: int, seed: int = 0) -> list[dict]:
    out = []
    for f in sorted(glob.glob(str(ROOT / "results" / "runs" / "*" / "*" / "autom2m" / "*.json"))):
        d = json.loads(Path(f).read_text())
        det = d.get("detail") or {}
        reps = det.get("fault_reports") or []
        if d.get("success") or d.get("status") != "failed" or not reps:
            continue
        out.append((f, d))
    random.Random(seed).shuffle(out)
    return out[:n]


def main(argv=None) -> int:
    from autom2m.attribution import FaultReport
    from autom2m.loop import json_from

    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=150)
    ap.add_argument("--workers", type=int, default=8)
    a = ap.parse_args(argv)
    specs = llms.coders()
    out = ROOT / "results" / "rq4" / "natural.jsonl"
    out.parent.mkdir(parents=True, exist_ok=True)

    def work(item):
        f, d = item
        rep = FaultReport(**{k: v for k, v in d["detail"]["fault_reports"][0].items() if k in FaultReport.__dataclass_fields__})
        resp = rep.responsible()
        labels = []
        for m, s in specs[:2]:
            llm = llms.make(m, s, max_tokens=150)
            try:
                r = json_from(llm.generate(PROMPT.format(run=render_run(d)), temperature=0.0, format="json")) or {}
            except Exception:  # noqa: BLE001
                r = {}
            labels.append({"agent": str(r.get("agent", "")).strip(), "binding": str(r.get("binding", "")).strip()})
        agree = labels[0] == labels[1]
        final = labels[0]
        return {"run": Path(f).stem, "bench": d["bench"], "fault_class": rep.fault_class,
                "lookup": {"agent": resp.get("agent"), "binding": f"{resp.get('rule')}.{resp.get('binding')}"},
                "coders": labels, "coders_agree": agree, "final": final,
                "agent_ok": final["agent"] == resp.get("agent"),
                "binding_ok": final["binding"] == f"{resp.get('rule')}.{resp.get('binding')}"}

    todo = items(a.n)
    print(f"{len(todo)} natural AutoM2M failures", flush=True)
    with ThreadPoolExecutor(a.workers) as ex, out.open("w") as fh:
        for r in ex.map(work, todo):
            fh.write(json.dumps(r) + "\n")
            fh.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
