"""Aggregate all results into LaTeX tables (docs/tables/*.tex), figures
(docs/figures/*.pdf) and one JSON of every number (results/summary.json).

    python -m evaluation.analysis.analyze
"""
from __future__ import annotations

import glob
import json
from collections import Counter, defaultdict
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

from .stats import bootstrap_ci, cliffs_delta, cohen_kappa, holm, mcnemar, paired_bootstrap_diff, wilcoxon, wilson  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
RAW = ROOT / "results" / "raw"
TAB = ROOT / "docs" / "tables"
FIG = ROOT / "docs" / "figures"
CONDS = ["single", "free", "critic", "schema", "typed_unchecked", "autom2m", "typed_ref"]
CLABEL = {"single": "Single", "free": "Free", "critic": "Critic", "schema": "Schema",
          "typed_unchecked": "Typed-unchecked", "autom2m": r"\textsc{AutoM2M}", "typed_ref": "Typed-ref"}
PLABEL = {"single": "Single", "free": "Free", "critic": "Critic", "schema": "Schema",
          "typed_unchecked": "Typed-unchk.", "autom2m": "AutoM2M", "typed_ref": "Typed-ref"}
MODELS = {"qwen2.5-coder:7b": "Qwen2.5-Coder-7B", "qwen3.8:27b": "Qwen3.8-27B"}
BENCH = {"classeval": "ClassEval", "humanevalplus": "HumanEval+"}
TEAM_CONDS = ["free", "critic", "schema", "typed_unchecked", "autom2m", "typed_ref"]
S: dict = {}  # every reported number


def load_rows() -> list[dict]:
    rows = {}
    for f in sorted(RAW.glob("*.jsonl")):
        if "_pilot" in f.name:
            continue
        for line in f.read_text().splitlines():
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            if r.get("status") == "error":
                continue
            rows[(r["bench"], r["model"], r["task"], r["condition"], r["seed"])] = r
    return list(rows.values())


def per_task(rows, key="success"):
    """task -> mean over seeds."""
    acc = defaultdict(list)
    for r in rows:
        acc[r["task"]].append(float(r[key]) if r.get(key) is not None else 0.0)
    return {t: sum(v) / len(v) for t, v in acc.items()}


def fmt(x, d=1, pct=True):
    if x != x:  # nan
        return "--"
    return f"{100 * x:.{d}f}" if pct else f"{x:.{d}f}"


# --------------------------------------------------------------------------
# RQ2: task success and cost
# --------------------------------------------------------------------------

def main_table(rows):
    lines = [r"\begin{tabular}{@{}l l r r r r r r r@{}}", r"\toprule",
             r"Bench. & Model & " + " & ".join(PLABEL[c] for c in CONDS) + r" \\", r"\midrule"]
    for bench in BENCH:
        for model in MODELS:
            sub = [r for r in rows if r["bench"] == bench and r["model"] == model]
            if not sub:
                continue
            tasks_all = sorted({r["task"] for r in sub})
            cells = []
            best = -1
            vals = {}
            for c in CONDS:
                rc = [r for r in sub if r["condition"] == c]
                pt = per_task(rc)
                common = [t for t in tasks_all if t in pt]
                m, lo, hi = bootstrap_ci([pt[t] for t in common])
                tp = per_task(rc, "test_pass_rate")
                mtp = sum(tp.values()) / len(tp) if tp else float("nan")
                seeds = sorted({r["seed"] for r in rc})
                S.setdefault("rq2", {}).setdefault(bench, {}).setdefault(model, {})[c] = {
                    "success": m, "ci": [lo, hi], "test_pass_rate": mtp, "n_tasks": len(common), "seeds": seeds,
                    "out_tokens": _mean([r["total"]["out_tokens"] for r in rc if r.get("total")]),
                    "in_tokens": _mean([r["total"]["in_tokens"] for r in rc if r.get("total")]),
                    "seconds": _mean([r["seconds"] for r in rc if r.get("seconds") is not None]),
                    "budget_hits": sum(1 for r in rc if r.get("budget_hit")), "runs": len(rc)}
                vals[c] = m
            best = max(v for v in vals.values() if v == v) if vals else -1
            for c in CONDS:
                d = S["rq2"][bench][model][c]
                cell = fmt(d["success"])
                if d["success"] == best:
                    cell = r"\textbf{" + cell + "}"
                cells.append(cell + r"{\scriptsize\,(" + fmt(d["test_pass_rate"], 0) + ")}")
            lines.append(f"{BENCH[bench]} & {MODELS[model]} & " + " & ".join(cells) + r" \\")
    lines += [r"\bottomrule", r"\end{tabular}"]
    (TAB / "rq2_success.tex").write_text("\n".join(lines) + "\n")


def _mean(xs):
    xs = [x for x in xs if x is not None]
    return sum(xs) / len(xs) if xs else float("nan")


def paired_tests(rows):
    """AutoM2M vs every other condition, per bench x model; Holm within cell."""
    lines = [r"\begin{tabular}{@{}l l l r r r r@{}}", r"\toprule",
             r"Bench. & Model & vs. & $\Delta$ success [95\% CI] & Wilcoxon $p_{\mathrm{Holm}}$ & McNemar s1 (+/--) & Cliff's $\delta$ \\",
             r"\midrule"]
    for bench in BENCH:
        for model in MODELS:
            sub = [r for r in rows if r["bench"] == bench and r["model"] == model]
            if not sub:
                continue
            a_rows = [r for r in sub if r["condition"] == "autom2m"]
            pa = per_task(a_rows)
            res, ps = {}, {}
            for c in CONDS:
                if c == "autom2m":
                    continue
                pb = per_task([r for r in sub if r["condition"] == c])
                common = sorted(set(pa) & set(pb))
                if len(common) < 5:
                    continue
                xa, xb = [pa[t] for t in common], [pb[t] for t in common]
                m, lo, hi = paired_bootstrap_diff(xa, xb)
                s1a = {r["task"]: r["success"] for r in a_rows if r["seed"] == 1}
                s1b = {r["task"]: r["success"] for r in sub if r["condition"] == c and r["seed"] == 1}
                cm = sorted(set(s1a) & set(s1b))
                n01, n10, pm = mcnemar([s1a[t] for t in cm], [s1b[t] for t in cm])
                ps[c] = wilcoxon(xa, xb)
                res[c] = (m, lo, hi, n01, n10, pm, cliffs_delta(xa, xb), len(common))
            adj = holm(ps)
            for c, (m, lo, hi, n01, n10, pm, cd, n) in res.items():
                S.setdefault("rq2_tests", {}).setdefault(bench, {}).setdefault(model, {})[c] = {
                    "diff": m, "ci": [lo, hi], "p_wilcoxon": ps[c], "p_holm": adj[c], "mcnemar": [n01, n10, pm],
                    "cliffs": cd, "n": n}
                lines.append(f"{BENCH[bench]} & {MODELS[model]} & {PLABEL[c]} & {100*m:+.1f} [{100*lo:+.1f}, {100*hi:+.1f}] & "
                             f"{adj[c]:.3f} & {n01}/{n10} & {cd:+.2f}" + r" \\")
            lines.append(r"\midrule")
    if lines[-1] == r"\midrule":
        lines.pop()
    lines += [r"\bottomrule", r"\end{tabular}"]
    (TAB / "rq2_tests.tex").write_text("\n".join(lines) + "\n")


def cost_table(rows):
    lines = [r"\begin{tabular}{@{}l l r r r r r r r@{}}", r"\toprule",
             r"Bench. & Model & " + " & ".join(PLABEL[c] for c in CONDS) + r" \\", r"\midrule"]
    for bench in BENCH:
        for model in MODELS:
            d = S.get("rq2", {}).get(bench, {}).get(model)
            if not d:
                continue
            cells = [f"{d[c]['out_tokens']/1000:.1f}k" if c in d else "--" for c in CONDS]
            lines.append(f"{BENCH[bench]} & {MODELS[model]} & " + " & ".join(cells) + r" \\")
    lines += [r"\bottomrule", r"\end{tabular}"]
    (TAB / "rq2_cost.tex").write_text("\n".join(lines) + "\n")


def admission_analysis(rows):
    """Admission outcome of builder-generated typed teams; natural defects in first proposals."""
    out = {}
    first_conds = defaultdict(Counter)
    for bench in BENCH:
        for model in MODELS:
            rc = [r for r in rows if r["bench"] == bench and r["model"] == model and r["condition"] == "autom2m"]
            if not rc:
                continue
            n = len(rc)
            not_adm = sum(1 for r in rc if r["status"] == "not_admitted")
            rounds = Counter(r.get("admission_rounds") for r in rc if r["status"] != "not_admitted")
            first_ok = sum(1 for r in rc if not r.get("first_diags"))
            phi = [r for r in rc if r.get("phi")]
            nophi = [r for r in rc if r["status"] in ("failed",)]
            out.setdefault(bench, {})[model] = {
                "runs": n, "not_admitted": not_adm, "first_proposal_admitted": first_ok, "rounds": dict(rounds),
                "phi_true": len(phi), "success_given_phi": _mean([float(r["success"]) for r in phi]),
                "success_given_failed": _mean([float(r["success"]) for r in nophi]),
                "repairs": _mean([r.get("repairs", 0) for r in rc]),
            }
            for r in rc:
                for d in r.get("first_diags") or []:
                    first_conds[(bench, model)][d[:2]] += 1
            # typed-unchecked: how many first proposals violate, and success with violations
            ru = [r for r in rows if r["bench"] == bench and r["model"] == model and r["condition"] == "typed_unchecked"]
            viol = [r for r in ru if r.get("first_diags")]
            out[bench][model]["unchecked_runs"] = len(ru)
            out[bench][model]["unchecked_with_violation"] = len(viol)
            out[bench][model]["unchecked_compile_error"] = sum(1 for r in ru if r["status"] == "compile_error")
            out[bench][model]["unchecked_success_violation"] = _mean([float(r["success"]) for r in viol])
            out[bench][model]["unchecked_success_clean"] = _mean([float(r["success"]) for r in ru if not r.get("first_diags")])
    S["admission"] = out
    S["first_proposal_conditions"] = {f"{b}|{m}": dict(c) for (b, m), c in first_conds.items()}


def completion_claims(rows):
    """D4: how reliable is 'done'? Free/Critic: TERMINATE claim; Schema: status=done;
    typed: engine predicate phi. Reports P(hidden success | declared done)."""
    out = {}
    for bench in BENCH:
        for model in MODELS:
            res = {}
            for c in ["free", "critic", "schema", "autom2m", "typed_ref"]:
                files = glob.glob(str(ROOT / "results" / "runs" / bench / model.replace(":", "_") / c / "*.json"))
                declared, succ_decl, n = 0, 0, 0
                for f in files:
                    try:
                        d = json.loads(Path(f).read_text())
                    except (json.JSONDecodeError, OSError):
                        continue
                    n += 1
                    det = d.get("detail") or {}
                    if c in ("free", "critic", "schema"):
                        dec = bool((det.get("extra") or {}).get("terminated_by"))
                    else:
                        dec = bool(det.get("phi"))
                    if dec:
                        declared += 1
                        succ_decl += bool(d.get("success"))
                if n:
                    lo, hi = wilson(succ_decl, declared)
                    res[c] = {"runs": n, "declared_done": declared, "success_given_done": succ_decl / declared if declared else float("nan"),
                              "ci": [lo, hi]}
            out.setdefault(bench, {})[model] = res
    S["completion"] = out


# --------------------------------------------------------------------------
# figures
# --------------------------------------------------------------------------

def fig_success():
    d = S.get("rq2", {})
    panels = [(b, m) for b in BENCH for m in MODELS if d.get(b, {}).get(m)]
    if not panels:
        return
    fig, axes = plt.subplots(1, len(panels), figsize=(3.4 * len(panels), 2.5), sharey=False)
    axes = [axes] if len(panels) == 1 else axes
    colors = ["#9e9e9e", "#d18f3f", "#c46b2e", "#8d6e63", "#7f9fc4", "#2f6db5", "#123f7a"]
    for ax, (b, m) in zip(axes, panels):
        vals = [d[b][m][c] for c in CONDS]
        x = range(len(CONDS))
        ax.bar(x, [100 * v["success"] for v in vals], color=colors,
               yerr=[[100 * (v["success"] - v["ci"][0]) for v in vals], [100 * (v["ci"][1] - v["success"]) for v in vals]],
               capsize=2, error_kw={"lw": 0.8})
        ax.set_xticks(list(x))
        ax.set_xticklabels([PLABEL[c] for c in CONDS], rotation=55, ha="right", fontsize=7)
        ax.set_title(f"{BENCH[b]} / {MODELS[m]}", fontsize=8)
        ax.set_ylabel("task success (%)", fontsize=7)
        ax.tick_params(axis="y", labelsize=7)
        ax.grid(axis="y", lw=0.3, alpha=0.6)
    fig.tight_layout()
    fig.savefig(FIG / "rq2_success.pdf")
    plt.close(fig)


def fig_cost():
    d = S.get("rq2", {})
    fig, ax = plt.subplots(figsize=(3.4, 2.4))
    marks = {"classeval": "o", "humanevalplus": "^"}
    cols = {"qwen2.5-coder:7b": "#d18f3f", "qwen3.8:27b": "#2f6db5"}
    for b in d:
        for m in d[b]:
            for c in CONDS:
                v = d[b][m][c]
                ax.scatter(v["out_tokens"] / 1000, 100 * v["success"], marker=marks[b], color=cols[m], s=14)
                if c in ("autom2m", "single", "free"):
                    ax.annotate(PLABEL[c], (v["out_tokens"] / 1000, 100 * v["success"]), fontsize=5, xytext=(2, 2),
                                textcoords="offset points")
    ax.set_xlabel("generated tokens per run (k)", fontsize=7)
    ax.set_ylabel("task success (%)", fontsize=7)
    ax.tick_params(labelsize=7)
    ax.grid(lw=0.3, alpha=0.6)
    fig.tight_layout()
    fig.savefig(FIG / "rq2_cost.pdf")
    plt.close(fig)


def fig_scale():
    p = ROOT / "results" / "rq2" / "scale.json"
    if not p.exists():
        return
    rows = json.loads(p.read_text())
    fig, ax = plt.subplots(figsize=(2.4, 1.8))
    ax.loglog([r["n_views"] for r in rows], [1000 * r["seconds"] for r in rows], "o-", color="#2f6db5", ms=3)
    ax.set_xlabel("views in chain team", fontsize=7)
    ax.set_ylabel("check time (ms)", fontsize=7)
    ax.tick_params(labelsize=7)
    ax.grid(lw=0.3, alpha=0.6, which="both")
    fig.tight_layout()
    fig.savefig(FIG / "rq2_scale.pdf")
    plt.close(fig)
    S["scale"] = rows


# --------------------------------------------------------------------------
# static checker study, RQ1, RQ3
# --------------------------------------------------------------------------

def mutation_table():
    from evaluation.rq2.mutate import OPERATORS

    res = {}
    for g in ("g1", "g2"):
        p = ROOT / "results" / "rq2" / f"mutants_{g}.jsonl"
        if not p.exists():
            continue
        agg = defaultdict(lambda: [0, 0, 0, 0])
        fa = 0
        for line in p.read_text().splitlines():
            r = json.loads(line)
            if r["operator"] == "none":
                fa += r["anchored_detected"]
                continue
            a = agg[r["operator"]]
            a[0] += 1
            a[1] += r["anchored_detected"]
            a[2] += r["naive_detected"]
            a[3] += bool(r["targeted_hit"])
        res[g] = {"ops": dict(agg), "false_alarms": fa}
    S["mutation"] = res
    if "g1" not in res:
        return
    lines = [r"\begin{tabular}{@{}l l r r r r r@{}}", r"\toprule",
             r"& & & \multicolumn{2}{c}{$G_1$ (checked)} & \multicolumn{2}{c}{$G_2$ (+delivered)} \\",
             r"\cmidrule(lr){4-5}\cmidrule(l){6-7}",
             r"Defect & Operator & Mut. & anch. & naive & anch. & naive \\", r"\midrule"]
    tot = [0, 0, 0, 0, 0]
    names = {"delete_rule": "delete a rule", "detach_goal": "detach goal data", "break_footprint": "break a footprint path",
             "drop_mandatory": "drop a mandatory binding", "second_writer": "add a second writer",
             "duplicate_rule": "duplicate a producing rule", "add_claim": "add an agent claim to $\\varphi$",
             "drop_clause": "drop an engine clause", "remove_tool": "remove a required tool"}
    for op, (d, _w) in OPERATORS.items():
        a1 = res["g1"]["ops"].get(op, [0, 0, 0, 0])
        a2 = res.get("g2", {"ops": {}})["ops"].get(op, [0, 0, 0, 0])
        lines.append(f"{d} & {names[op]} & {a1[0]} & {a1[1]} & {a1[2]} & {a2[1]} & {a2[2]}" + r" \\")
        for i, v in enumerate([a1[0], a1[1], a1[2], a2[1], a2[2]]):
            tot[i] += v
    lines += [r"\midrule", f"& Total & {tot[0]} & {tot[1]} & {tot[2]} & {tot[3]} & {tot[4]}" + r" \\", r"\bottomrule", r"\end{tabular}"]
    (TAB / "rq2_mutation.tex").write_text("\n".join(lines) + "\n")


def rq1_analysis():
    out = {}
    for src in ("whowhen", "ours"):
        p = ROOT / "results" / "rq1" / f"codes_{src}.jsonl"
        if not p.exists():
            continue
        rows = [json.loads(l) for l in p.read_text().splitlines()]
        a = [r["coder_27b"]["code"] for r in rows]
        b = [r["coder_7b"]["code"] for r in rows]
        comp = lambda c: c.startswith("D")  # noqa: E731
        groups = defaultdict(list)
        for r in rows:
            groups[(r.get("bench", "gaia"), r.get("model", "-"), r["condition"])].append(r)
        cells = {}
        for k, rs in groups.items():
            vals = [1.0 if comp(r["final"]) else 0.0 for r in rs]
            m, lo, hi = bootstrap_ci(vals)
            cells["|".join(k)] = {"n": len(rs), "composition_share": m, "ci": [lo, hi],
                                  "codes": dict(Counter(r["final"] for r in rs))}
        out[src] = {"n": len(rows), "kappa": cohen_kappa(a, b), "agreement": sum(x == y for x, y in zip(a, b)) / max(1, len(rows)),
                    "kappa_binary": cohen_kappa(["C" if comp(x) else "N" for x in a], ["C" if comp(x) else "N" for x in b]),
                    "cells": cells, "codes": dict(Counter(r["final"] for r in rows))}
    S["rq1"] = out


def rq3_analysis():
    out = {}
    for p in sorted((ROOT / "results" / "rq3").glob("attribution__*.jsonl")):
        model = p.stem.split("__")[1]
        rows = [json.loads(l) for l in p.read_text().splitlines()]
        res = {}
        for meth in ["trace", "all_at_once", "step_by_step", "binary_search"]:
            per = defaultdict(lambda: defaultdict(list))
            for r in rows:
                x = r[meth]
                for k in ("agent_ok", "binding_ok", "class_ok"):
                    per[r["fault"]][k].append(bool(x.get(k)))
                    per["all"][k].append(bool(x.get(k)))
                per[r["fault"]]["calls"].append(x.get("calls") or 0)
                per["all"]["calls"].append(x.get("calls") or 0)
            res[meth] = {f: {k: (sum(v) / len(v) if v else float("nan")) for k, v in d.items()} | {"n": len(d["agent_ok"])}
                         for f, d in per.items()}
        out[model] = res
    for p in sorted((ROOT / "results" / "rq3").glob("inject__*.jsonl")):
        model = p.stem.split("__")[1]
        rows = [json.loads(l) for l in p.read_text().splitlines() if '"error"' not in l[:2000]]
        man = Counter(r["fault"] for r in rows if r.get("manifested"))
        tot = Counter(r["fault"] for r in rows)
        out.setdefault(model, {})["manifested"] = {f: [man[f], tot[f]] for f in tot}
        rep = [{"inplace": r["repair"]["inplace"], "rebuild": r["repair"].get("rebuild") or {},
                "kept": r["repair"]["info"].get("kept"), "before": r["repair"]["info"].get("before"),
                "mode": r["repair"]["info"].get("mode"), "fault": r["fault"]}
               for r in rows if r.get("manifested") and r.get("repair")]
        out[model]["repair"] = summarize_repair(rep)
        out[model]["repair_by_fault"] = {f: summarize_repair([x for x in rep if x["fault"] == f]) for f in sorted({x["fault"] for x in rep})}
    S["rq3"] = out


def summarize_repair(rows):
    if not rows:
        return {}
    def m(k, sub):
        return _mean([r[sub].get(k) for r in rows if r.get(sub)])
    return {"n": len(rows),
            "inplace_phi": m("phi", "inplace"), "rebuild_phi": m("phi", "rebuild"),
            "inplace_success": m("success", "inplace"), "rebuild_success": m("success", "rebuild"),
            "inplace_tokens": m("out_tokens", "inplace"), "rebuild_tokens": m("out_tokens", "rebuild"),
            "kept": _mean([r.get("kept") for r in rows]), "before": _mean([r.get("before") for r in rows])}


def _macro(name: str, value) -> str:
    return f"\\newcommand{{\\n{name}}}{{{value}}}"


def write_numbers(rows) -> None:
    """LaTeX macros for every number the prose cites (docs/tables/numbers.tex)."""
    import os

    m = {"CanonCE": 96, "CanonHE": 163, "Budget": f"{int(os.environ.get('AM2M_BUDGET_OUT', '24000')):,}".replace(",", "{,}"),
         "RunsTotal": f"{len(rows):,}".replace(",", "{,}"),
         "CESubset": sum(1 for l in (ROOT / "evaluation" / "ce_40.txt").read_text().split()),
         "HESubset": sum(1 for l in (ROOT / "evaluation" / "he_quarter.txt").read_text().split())}
    lines = [_macro(k, v) for k, v in m.items()]
    for b, bm in S.get("rq2", {}).items():
        for mod, cm in bm.items():
            tag = ("CE" if b == "classeval" else "HE") + ("Sm" if mod.startswith("qwen2.5") else "Lg")
            for c, v in cm.items():
                ctag = "".join(w.capitalize() for w in c.split("_"))
                lines.append(_macro(f"S{tag}{ctag}", fmt(v["success"])))
                lines.append(_macro(f"T{tag}{ctag}", fmt(v["test_pass_rate"])))
                lines.append(_macro(f"K{tag}{ctag}", f"{v['out_tokens']/1000:.1f}k" if v["out_tokens"] == v["out_tokens"] else "--"))
    # audit (RQ1), mutation and natural mutants (RQ2 static), scaling
    a = ROOT / "results" / "rq1" / "audit_whowhen.json"
    if a.exists():
        au = json.loads(a.read_text())
        for k, v in {**au["algorithm_generated"], **{"hc_" + k: v for k, v in au["hand_crafted"].items()}}.items():
            lines.append(_macro("Aud" + "".join(w.capitalize() for w in k.split("_")), v))
    for g, d in S.get("mutation", {}).items():
        n = sum(v[0] for v in d["ops"].values())
        lines += [_macro(f"Mut{g.upper()}N", n), _macro(f"Mut{g.upper()}Anch", sum(v[1] for v in d["ops"].values())),
                  _macro(f"Mut{g.upper()}Naive", sum(v[2] for v in d["ops"].values())),
                  _macro(f"Mut{g.upper()}Target", sum(v[3] for v in d["ops"].values()))]
    nm = ROOT / "results" / "rq2" / "natural_mutants.json"
    if nm.exists():
        d = json.loads(nm.read_text())
        for mod, ops in d["operators"].items():
            tag = "Sm" if mod.startswith("qwen2.5") else "Lg"
            lines += [_macro(f"Nat{tag}Teams", d["teams"].get(mod, 0)),
                      _macro(f"Nat{tag}N", sum(v[0] for k, v in ops.items() if k != "detach_goal")),
                      _macro(f"Nat{tag}Det", sum(v[1] for k, v in ops.items() if k != "detach_goal")),
                      _macro(f"Nat{tag}Detach", ops.get("detach_goal", [0])[0])]
    for r in S.get("scale", []):
        lines.append(_macro(f"Scale{ {10:'Ten',30:'Thirty',100:'Hundred',300:'ThreeHundred',1000:'Thousand',3000:'ThreeThousand'}[r['n_views']] }",
                            f"{1000*r['seconds']:.1f}"))
    (TAB / "numbers.tex").write_text("\n".join(lines) + "\n")


def main() -> int:
    TAB.mkdir(parents=True, exist_ok=True)
    FIG.mkdir(parents=True, exist_ok=True)
    rows = load_rows()
    S["n_runs"] = len(rows)
    S["runs_by_cell"] = dict(Counter(f"{r['bench']}|{r['model']}|{r['condition']}" for r in rows))
    if rows:
        main_table(rows)
        paired_tests(rows)
        cost_table(rows)
        admission_analysis(rows)
        completion_claims(rows)
        fig_success()
        fig_cost()
    fig_scale()
    mutation_table()
    rq1_analysis()
    rq3_analysis()
    write_numbers(rows)
    (ROOT / "results" / "summary.json").write_text(json.dumps(S, indent=1, default=str))
    print(json.dumps({k: S[k] for k in ("n_runs",) if k in S}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
