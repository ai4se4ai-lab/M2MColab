"""hpc/scripts/summarize.py — per-model tables for analysis (run by the 40_analysis stage).

Complements evaluation.analysis.analyze (which writes the paper's tables, figures and
summary.json) with flat, per-model views of every run and a completeness check of
every research question. Writes to <results>/hpc_summary/:

  matrix_long.csv        one row per run (all summary fields, token counts flattened)
  success_by_cell.csv    model x bench x condition: runs, mean success, task-level
                         majority-over-seeds success, mean hidden-test pass rate
  success_by_seed.csv    model x bench x condition x seed
  done_signal.csv        RQ3/H3c: precision, lift, recall, F1 of each condition's own "done"
  cost.csv               RQ3 cost: median/mean output tokens and seconds, budget hits
  admission.csv          RQ2: AutoM2M/Typed-* admission, rounds, first violation, refusals
  status.csv             run status counts (done / failed / not_admitted / error ...)
  completeness.csv       done vs expected per model x bench x condition x seed
  rq_outputs.csv         every RQ output file, rows, and whether it is present
  steps.csv              the step ledger (latest attempt per step) with return codes
  model_digests.json     Ollama digests, quantisation and GPU share recorded by setup
  summary.md             the above as markdown tables
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

N_TASKS = {"classeval": 100, "humanevalplus": 164}
RQ_FILES = {
    "RQ1": ["rq1/audit_whowhen.json", "rq1/codes_whowhen.jsonl", "rq1/codes_ours.jsonl"],
    "RQ2": ["rq2/mutants.jsonl", "rq2/scale.json", "rq2/natural_mutants.json", "rq2/seeded.jsonl", "rq2/clean.jsonl",
            "rq2/independent.jsonl", "rq2/builder_ablation__qwen2.5-coder_7b.jsonl"],
    "RQ3": ["rq3/codes_runs.jsonl", "data/runs.csv", "data/rq3_codes.csv", "docs_out/tables/tab_success_classeval.tex",
            "docs_out/tables/tab_success_humaneval.tex", "docs_out/tables/tab_compfail.tex", "docs_out/tables/tab_donecost.tex"],
    "RQ4": ["rq4/natural.jsonl", "data/rq4_inject.csv", "data/rq4_transcript.csv", "data/rq4_repair.csv"],
    "all": ["summary.json", "report.md", "docs_out/figures/fig_rq1_causes.pdf", "docs_out/figures/fig_independent.pdf",
            "docs_out/figures/fig_admission.pdf", "docs_out/figures/fig_scale_a.pdf", "docs_out/figures/fig_attribution.pdf"],
}


def load_raw(res: Path) -> pd.DataFrame:
    recs = []
    for f in sorted((res / "raw").glob("*.jsonl")):
        for line in f.read_text().splitlines():
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            for k in ("total", "tokens"):
                v = r.pop(k, None)
                if isinstance(v, dict):
                    for kk, vv in v.items():
                        if isinstance(vv, dict):
                            for k3, v3 in vv.items():
                                r[f"{k}.{kk}.{k3}"] = v3
                        else:
                            r[f"{k}.{kk}"] = vv
            for k, v in list(r.items()):
                if isinstance(v, (list, dict)):
                    r[k] = json.dumps(v)
            r["_file"] = f.name
            recs.append(r)
    df = pd.DataFrame(recs)
    if df.empty:
        return df
    # the latest record of a (bench, model, task, condition, seed) wins, as in analysis/export.py
    return df.drop_duplicates(["bench", "model", "task", "condition", "seed"], keep="last")


def md(df: pd.DataFrame, floatfmt: str = ".3f") -> str:
    if df is None or df.empty:
        return "_none_\n"
    try:
        return df.to_markdown(index=False, floatfmt=floatfmt) + "\n"
    except ImportError:  # tabulate not installed
        return "```\n" + df.to_string(index=False) + "\n```\n"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--results", required=True)
    ap.add_argument("--logs", default="")
    a = ap.parse_args()
    res = Path(a.results)
    out = res / "hpc_summary"
    out.mkdir(parents=True, exist_ok=True)
    L = [f"# HPC summary — {res}\n"]

    df = load_raw(res)
    if not df.empty:
        df.to_csv(out / "matrix_long.csv", index=False)
        ok = df[df.status != "error"].copy()
        ok["success"] = ok.success.astype(bool)
        g = ok.groupby(["model", "bench", "condition"])
        maj = (ok.groupby(["model", "bench", "condition", "task"]).success.mean() > 0.5).groupby(level=[0, 1, 2]).mean()
        cell = g.agg(runs=("success", "size"), success=("success", "mean"), pass_rate=("test_pass_rate", "mean")).reset_index()
        cell = cell.merge(maj.rename("majority_success").reset_index(), on=["model", "bench", "condition"])
        cell.to_csv(out / "success_by_cell.csv", index=False)
        ok.groupby(["model", "bench", "condition", "seed"]).success.agg(["size", "mean"]).reset_index() \
          .rename(columns={"size": "runs", "mean": "success"}).to_csv(out / "success_by_seed.csv", index=False)

        # RQ3/H3c: precision of each condition's own completion signal
        rows = []
        for (m, b, c), s in ok.groupby(["model", "bench", "condition"]):
            dd = s[s.declared_done.astype(bool)]
            base = s.success.mean()
            prec = dd.success.mean() if len(dd) else float("nan")
            rec = (s.success & s.declared_done.astype(bool)).sum() / max(s.success.sum(), 1)
            rows.append({"model": m, "bench": b, "condition": c, "runs": len(s), "declared_done": len(dd),
                         "precision": prec, "base_rate": base, "lift": prec / base if base else float("nan"),
                         "recall": rec, "f1": 2 * prec * rec / (prec + rec) if prec == prec and (prec + rec) else float("nan")})
        done = pd.DataFrame(rows)
        done.to_csv(out / "done_signal.csv", index=False)

        tok = "total.out_tokens" if "total.out_tokens" in ok else None
        cost = g.agg(median_seconds=("seconds", "median"), mean_seconds=("seconds", "mean")).reset_index()
        if tok:
            cost = cost.merge(g[tok].agg(["median", "mean"]).rename(columns={"median": "median_out_tokens",
                                                                              "mean": "mean_out_tokens"}).reset_index(),
                              on=["model", "bench", "condition"])
        if "budget_hit" in ok:
            cost = cost.merge(g.budget_hit.apply(lambda x: x.fillna(False).astype(bool).mean()).rename("budget_hit_share")
                              .reset_index(), on=["model", "bench", "condition"])
        cost.to_csv(out / "cost.csv", index=False)

        typed = ok[ok.condition.isin(["autom2m", "typed_nc", "typed_ref", "autom2m_1ex"])]
        adm = pd.DataFrame()
        if not typed.empty and "admitted" in typed:
            adm = typed.groupby(["model", "bench", "condition"]).agg(
                runs=("task", "size"), admitted=("admitted", lambda x: x.fillna(False).astype(bool).mean()),
                mean_round=("admitted_round", "mean"),
                not_admitted=("status", lambda x: (x == "not_admitted").mean())).reset_index()
            if "first_violation" in typed:
                fv = typed.first_violation.dropna().astype(str).str[:2].value_counts(normalize=True)
                L.append("First violation of rejected proposals (all typed runs): "
                         + ", ".join(f"{k} {v:.0%}" for k, v in fv.items()) + "\n")
            adm.to_csv(out / "admission.csv", index=False)
        st = df.groupby(["model", "bench", "condition", "status"]).size().unstack(fill_value=0).reset_index()
        st.to_csv(out / "status.csv", index=False)

        comp = []
        for (m, b, c, s), n in ok.groupby(["model", "bench", "condition", "seed"]).size().items():
            comp.append({"model": m, "bench": b, "condition": c, "seed": s, "done": n, "expected": N_TASKS.get(b),
                         "complete": n >= N_TASKS.get(b, 0)})
        comp = pd.DataFrame(comp)
        comp.to_csv(out / "completeness.csv", index=False)

        L.append(f"Runs: {len(df)} recorded, {len(ok)} not in error, {int((df.status == 'error').sum())} in error.\n")
        L.append("\n## RQ3 — success by model, benchmark and condition (mean over seeds)\n")
        for b in sorted(ok.bench.unique()):
            piv = cell[cell.bench == b].pivot(index="model", columns="condition", values="success").reset_index()
            L.append(f"\n### {b}\n\n" + md(piv))
        L.append("\n## RQ3 — precision of \"done\" (H3c)\n\n" + md(done))
        L.append("\n## RQ3 — cost\n\n" + md(cost, ".1f"))
        L.append("\n## RQ2 — admission of typed teams\n\n" + md(adm))
        inc = comp[~comp.complete] if not comp.empty else comp
        L.append(f"\n## Completeness\n\n{len(comp) - len(inc)}/{len(comp)} model x bench x condition x seed cells complete.\n")
        if not inc.empty:
            L.append(md(inc))
    else:
        L.append("No matrix runs yet.\n")

    rq = []
    for q, files in RQ_FILES.items():
        for f in files:
            p = res / f
            n = (sum(1 for _ in p.open()) if p.suffix in (".jsonl", ".csv") else None) if p.exists() else None
            rq.append({"rq": q, "file": f, "present": p.exists(), "rows": n,
                       "kib": round(p.stat().st_size / 1024, 1) if p.exists() else None})
    for f in sorted((res / "rq4").glob("*.jsonl")) if (res / "rq4").exists() else []:
        rq.append({"rq": "RQ4", "file": f"rq4/{f.name}", "present": True, "rows": sum(1 for _ in f.open()),
                   "kib": round(f.stat().st_size / 1024, 1)})
    rqdf = pd.DataFrame(rq)
    rqdf.to_csv(out / "rq_outputs.csv", index=False)
    L.append("\n## Research-question outputs\n\n" + md(rqdf))

    if a.logs and (Path(a.logs) / "steps.tsv").exists():
        led = pd.read_csv(Path(a.logs) / "steps.tsv", sep="\t")
        latest = led.drop_duplicates(["stage", "step"], keep="last")
        latest.to_csv(out / "steps.csv", index=False)
        bad = latest[latest.rc != 0]
        L.append(f"\n## Pipeline steps\n\n{len(latest)} steps, {len(bad)} whose latest attempt failed.\n\n"
                 + md(bad[["stage", "step", "rc", "job", "end"]] if not bad.empty else bad))
    dig = res.parent / "setup" / "model_digests.json"
    if dig.exists():
        (out / "model_digests.json").write_text(dig.read_text())
        d = json.loads(dig.read_text())
        L.append("\n## Models (from setup)\n\n" + md(pd.DataFrame(
            [{"model": k, **{kk: v.get(kk) for kk in ("parameter_size", "quantization", "digest", "gpu_share")}}
             for k, v in d.items()])))
    (out / "summary.md").write_text("\n".join(L))
    print("wrote", out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
