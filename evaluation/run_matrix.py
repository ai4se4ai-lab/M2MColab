"""Run conditions x tasks x seeds for one model; resumable (paper Sec. 4.2).

    python -m evaluation.run_matrix --bench classeval --model qwen2.5-coder:7b --seeds 1,2,3 --workers 12

Conditions (Table 6): single, single_gate, free, critic, schema, typed_nc,
autom2m, typed_ref. `autom2m_1ex` is the preliminary one-example builder
configuration of RQ2 (example copying). Single-Gate's token budget is
AutoM2M's median output tokens for the same model and benchmark, so run it
after autom2m (the default order does).

One summary line per run goes to results/raw/<bench>__<model>.jsonl; the
full record (transcript / typed-team outcome, final code, call log) to
results/runs/<bench>/<model>/<condition>/<task>__s<seed>.json.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import statistics
import time
import traceback
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RESULTS = Path(os.environ.get("AM2M_RESULTS", ROOT / "results"))
OLLAMA = os.environ.get("AM2M_OLLAMA", "http://127.0.0.1:11435")
BUDGET_OUT = int(os.environ.get("AM2M_BUDGET_OUT", "60000"))  # safety cap on generated tokens per run
GATE_DEFAULT = int(os.environ.get("AM2M_GATE_BUDGET", "9000"))
BUILDER = os.environ.get("AM2M_BUILDER", "v1")  # "v1" (paper Sec. 3) or "v2" (checked builder, autom2m/builder.py)
CONDITIONS = ["single", "free", "critic", "schema", "typed_nc", "autom2m", "typed_ref", "single_gate"]
TYPED = ("typed_nc", "autom2m", "typed_ref", "autom2m_1ex")
LABEL = {"single": "Single", "single_gate": "Single-Gate", "free": "Free", "critic": "Critic", "schema": "Schema",
         "typed_nc": "Typed-NC", "autom2m": "AutoM2M", "typed_ref": "Typed-Ref", "autom2m_1ex": "AutoM2M (1 example)"}


def _safe(s: str) -> str:
    return s.replace(":", "_").replace("/", "_")


def make_llm(model: str, seed: int, *, keep_text: bool = False, budget: int | None = BUDGET_OUT):
    from agenthot.llm.metered import MeteredBackend
    from agenthot.llm.ollama_backend import OllamaBackend

    return MeteredBackend(OllamaBackend(OLLAMA, model, seed=seed, num_ctx=16384, timeout=900, max_tokens=4096),
                          budget_out=budget, keep_text=keep_text)


def team_shape(team: dict | None) -> str:
    """A coarse signature of a typed team (agents, views, validators) used to
    count distinct team shapes and copies of the worked examples."""
    if not isinstance(team, dict):
        return ""
    agents = sorted(str(a.get("name")) for a in team.get("agents", []) if isinstance(a, dict))
    views = sorted(v for v in (team.get("views") or {}) if v != "Goal")
    vals = sorted({str(v.get("id")) for h in team.get("handoffs", []) or [] for r in h.get("rules", []) or []
                   for b in r.get("llm", []) or [] for v in (b.get("validator") or []) if isinstance(v, dict)})
    return f"{len(agents)}|{','.join(agents)}|{','.join(views)}|{','.join(vals)}"


def run_one(bench: str, task_id: str, condition: str, model: str, seed: int, gate_budget: int = GATE_DEFAULT) -> dict:
    from autom2m.loop import AutoM2M
    from evaluation.benchmarks import tasks as T
    from evaluation.conditions import baselines as B
    from evaluation.harness.workbench import TaskWorkbench

    task = {t.task_id: t for t in T.load(bench)}[task_id]
    llm = make_llm(model, seed)
    t0 = time.time()
    detail: dict = {}
    status, code, declared = "done", "", False
    workdir = Path(os.environ.get("TMPDIR", "/tmp")) / f"am2m_{_safe(model)}_{condition}_{_safe(task_id)}_{seed}"
    try:
        if condition in ("single", "free", "critic", "schema", "single_gate"):
            if condition == "single_gate":
                rec = B.run_single_gate(task, llm, budget_out=gate_budget)
            else:
                rec = {"single": B.run_single, "free": B.run_free, "critic": B.run_critic,
                       "schema": B.run_schema}[condition](task, llm)
            code, status, declared = rec.code, rec.status, rec.declared_done
            detail = {"transcript": rec.transcript, "team": rec.team, "extra": rec.extra}
        else:
            wb = TaskWorkbench(task)
            initial = None
            if condition == "typed_ref":
                initial = json.loads((ROOT / "teams" / "classeval_reference.json").read_text())
            am = AutoM2M(llm, check_enabled=condition != "typed_nc", repair_enabled=condition in ("autom2m", "typed_ref", "autom2m_1ex"),
                         n_examples=1 if condition == "autom2m_1ex" else 3, builder=BUILDER, workdir=workdir)
            out = am.solve(task, task.prompt, wb, initial_team=initial)
            status = out.status
            declared = bool(out.phi)
            bodies = {k: (T.extract_function(v, k) or v) for k, v in out.deliverables.items()}
            code = T.assemble(task, bodies) if bodies else ""
            detail = out.to_dict()
    except Exception as exc:  # noqa: BLE001
        status = "error"
        detail = {"error": f"{type(exc).__name__}: {exc}", "trace": traceback.format_exc()[-3000:]}
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
    secs = time.time() - t0
    sc = T.score(task, code)
    row = {
        "bench": bench, "task": task_id, "condition": condition, "model": model, "seed": seed,
        "status": status, "success": bool(sc.get("success")), "test_pass_rate": float(sc.get("test_pass_rate", 0.0)),
        "tests_run": sc.get("tests_run"), "tests_passed": sc.get("tests_passed"), "declared_done": bool(declared),
        "seconds": round(secs, 1), "builder": BUILDER if condition in TYPED else None, "tokens": llm.by_role(), "total": llm.totals(), "budget_hit": llm.budget_hit,
    }
    if condition in ("single", "free", "critic", "schema", "single_gate"):
        ex = detail.get("extra") or {}
        row.update(turns=ex.get("turns"), terminated_by=ex.get("terminated_by"), candidates=ex.get("candidates"),
                   gate_budget=gate_budget if condition == "single_gate" else None,
                   critic_defects=len((ex.get("critique") or {}).get("defects") or []) if condition == "critic" else None)
    if condition in TYPED:
        row.update(admitted=detail.get("admitted_round") is not None, admitted_round=detail.get("admitted_round"),
                   admission_rounds=detail.get("admission_rounds"), first_violation=detail.get("first_violation"),
                   first_diags=(detail.get("diagnostics") or [[]])[0][:8], unparsable=detail.get("unparsable"),
                   checks=detail.get("checks"), check_seconds=detail.get("check_seconds"),
                   phi=detail.get("phi"), clauses=detail.get("clauses"), escalations=detail.get("escalations"),
                   repairs=[r.get("mode") for r in detail.get("repairs") or []],
                   repair_sources=[r.get("source") for r in detail.get("repairs") or []],
                   builder_unchanged=[r.get("builder_unchanged") for r in detail.get("repairs") or [] if "builder_unchanged" in r],
                   fault_classes=[f.get("fault_class") for f in detail.get("fault_reports") or []],
                   attribution_calls=detail.get("attribution_calls"), timing=detail.get("timing"),
                   team_shape=team_shape(detail.get("final_team")), first_shape=team_shape(detail.get("first_team")),
                   unchecked_violations=sorted({d[:2] for d in detail.get("unchecked_diagnostics") or []}))
    out_dir = RESULTS / "runs" / bench / _safe(model) / condition
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / f"{_safe(task_id)}__s{seed}.json").write_text(
        json.dumps({**row, "code": code, "score": sc, "detail": detail, "calls": llm.dump()}, default=str))
    return row


def raw_path(bench: str, model: str, tag: str = "") -> Path:
    return RESULTS / "raw" / f"{bench}__{_safe(model)}{tag}.jsonl"


def done_keys(path: Path) -> set:
    keys = set()
    if path.exists():
        for line in path.read_text().splitlines():
            try:
                r = json.loads(line)
                if r.get("status") != "error":
                    keys.add((r["task"], r["condition"], r["seed"]))
            except (json.JSONDecodeError, KeyError):
                pass
    return keys


def gate_budget(bench: str, model: str, tag: str = "") -> int:
    """AutoM2M's median output tokens per task (same model and benchmark)."""
    p = raw_path(bench, model, tag)
    toks = []
    if p.exists():
        for line in p.read_text().splitlines():
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            if r.get("condition") == "autom2m" and r.get("status") != "error":
                toks.append(r.get("total", {}).get("out_tokens", 0))
    return int(statistics.median(toks)) if len(toks) >= 5 else GATE_DEFAULT


def select_tasks(bench: str, spec: str) -> list[str]:
    from evaluation.benchmarks import tasks as T

    all_ids = [t.task_id for t in T.load(bench)]
    if spec.startswith("first:"):
        return all_ids[: int(spec.split(":")[1])]
    if spec.startswith("file:"):
        return [l.strip() for l in Path(spec[5:]).read_text().splitlines() if l.strip()]
    if spec.startswith("every:"):
        k = int(spec.split(":")[1])
        return all_ids[::k]
    return spec.split(",") if spec else all_ids


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--bench", required=True, choices=["classeval", "humanevalplus"])
    ap.add_argument("--model", required=True)
    ap.add_argument("--conditions", default=",".join(CONDITIONS))
    ap.add_argument("--seeds", default="1,2,3")
    ap.add_argument("--tasks", default="", help="comma list of task ids, 'first:N', 'every:K' or 'file:path'")
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--tag", default="")
    a = ap.parse_args(argv)
    ids = select_tasks(a.bench, a.tasks)
    conds = a.conditions.split(",")
    seeds = [int(s) for s in a.seeds.split(",")]
    out = raw_path(a.bench, a.model, a.tag)
    out.parent.mkdir(parents=True, exist_ok=True)
    # Single-Gate needs AutoM2M's median: run it in a second phase
    phases = [[c for c in conds if c != "single_gate"], [c for c in conds if c == "single_gate"]]
    for phase in phases:
        if not phase:
            continue
        done = done_keys(out)
        budget = gate_budget(a.bench, a.model, a.tag) if "single_gate" in phase else GATE_DEFAULT
        # seed-major order: complete seed 1 everywhere before seed 2 (graceful truncation)
        jobs = [(a.bench, t, c, a.model, s, budget) for s in seeds for t in ids for c in phase if (t, c, s) not in done]
        print(f"{len(jobs)} runs to do ({len(done)} already done) -> {out}" +
              (f" [single_gate budget {budget} tokens]" if "single_gate" in phase else ""), flush=True)
        t0 = time.time()
        with ProcessPoolExecutor(a.workers) as ex, out.open("a") as fh:
            futs = {ex.submit(run_one, *j): j for j in jobs}
            for i, f in enumerate(as_completed(futs), 1):
                j = futs[f]
                try:
                    row = f.result()
                except Exception as exc:  # noqa: BLE001
                    row = {"bench": j[0], "task": j[1], "condition": j[2], "model": j[3], "seed": j[4], "status": "error",
                           "success": False, "error": str(exc)[:300]}
                fh.write(json.dumps(row) + "\n")
                fh.flush()
                el = time.time() - t0
                print(f"[{i}/{len(jobs)} {el/60:.0f}m eta {el/i*(len(jobs)-i)/3600:.1f}h] {row['task']} {row['condition']} "
                      f"s{row['seed']} {row['status']} ok={row['success']} done={row.get('declared_done')} "
                      f"rate={row.get('test_pass_rate', 0):.2f} {row.get('seconds', 0)}s "
                      f"out_tok={row.get('total', {}).get('out_tokens')}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
