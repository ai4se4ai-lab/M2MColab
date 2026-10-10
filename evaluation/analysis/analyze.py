"""Every number, table and figure of the evaluation (paper Sec. 4), from
results/data/*.csv (written by export.py, which this module runs first).

    python -m evaluation.analysis.analyze

Outputs
  docs/tables/tab_mutation.tex, tab_success_classeval.tex, tab_success_humaneval.tex,
              tab_compfail.tex, tab_donecost.tex
  docs/figures/fig_rq1_causes.pdf, fig_independent.pdf, fig_admission.pdf,
               fig_scale_a.pdf .. fig_scale_d.pdf, fig_attribution.pdf
  results/summary.json  (every reported number)
"""
from __future__ import annotations

import argparse
import json
import math
import statistics
from collections import Counter, defaultdict
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import pandas as pd  # noqa: E402

from .stats import (  # noqa: E402
    cliff_magnitude,
    cliffs_delta,
    cluster_bootstrap,
    cochran_q,
    cohen_kappa,
    gee_interaction,
    gee_logit,
    holm,
    mcnemar,
    paired_bootstrap_diff,
    wilson,
)

import os

ROOT = Path(__file__).resolve().parents[2]
RESULTS = Path(os.environ.get("AM2M_RESULTS", ROOT / "results"))
DATA = RESULTS / "data"
DOCS = Path(os.environ.get("AM2M_DOCS_OUT", ROOT / "docs"))
TAB = DOCS / "tables"
FIG = DOCS / "figures"
CONDS = ["single", "single_gate", "free", "critic", "schema", "typed_nc", "autom2m", "typed_ref"]
LAB = {"single": "Single", "single_gate": "Single-Gate", "free": "Free", "critic": "Critic", "schema": "Schema",
       "typed_nc": "Typed-NC", "autom2m": "AutoM2M", "typed_ref": "Typed-Ref", "autom2m_1ex": "AutoM2M (1 ex.)"}
MODEL_LAB = {"qwen2.5-coder:7b": "Qwen-7B", "qwen2.5-coder:32b": "Qwen-32B", "devstral:24b": "Devstral",
             "deepseek-coder-v2:16b": "DSC-V2", "granite-code:20b": "Granite", "codellama:13b": "CodeLlama",
             "starcoder2:15b": "StarCoder2"}
BENCH_LAB = {"classeval": "ClassEval", "humanevalplus": "HumanEval+"}
COMP = {"D1", "D2", "D3", "D4", "D5"}
S: dict = {}
# muted, colour-blind-safe palette (validated categorical order)
PAL = ["#4C72B0", "#DD8452", "#55A868", "#C44E52", "#8172B3", "#937860", "#DA8BC3", "#8C8C8C", "#CCB974", "#64B5CD"]


def _read(name: str) -> pd.DataFrame:
    p = DATA / name
    if not p.exists() or p.stat().st_size == 0:
        return pd.DataFrame()
    try:
        return pd.read_csv(p)
    except pd.errors.EmptyDataError:
        return pd.DataFrame()


def pct(x, d=1) -> str:
    return "--" if x is None or x != x else f"{100 * x:.{d}f}"


def _save(fig, name: str) -> None:
    FIG.mkdir(parents=True, exist_ok=True)
    fig.savefig(FIG / name, bbox_inches="tight")
    plt.close(fig)


def _style(ax) -> None:
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.grid(axis="x", color="#e5e5e5", linewidth=0.6)
    ax.set_axisbelow(True)


def mlabel(m: str) -> str:
    return MODEL_LAB.get(m, m)


# ==========================================================================
# groups of models
# ==========================================================================

def model_groups(runs: pd.DataFrame) -> dict[str, str]:
    """Strong: the four models with the highest Single success on ClassEval."""
    sub = runs[(runs.condition == "single") & (runs.bench == "classeval")]
    if sub.empty:
        sub = runs[runs.condition == "single"]
    rank = sub.groupby("model").success.mean().sort_values(ascending=False)
    strong = set(rank.index[:4])
    return {m: ("strong" if m in strong else "weak") for m in runs.model.unique()}


# ==========================================================================
# RQ1
# ==========================================================================

def rq1() -> None:
    df = _read("rq1_codes.csv")
    out = {}
    if df.empty:
        S["rq1"] = out
        return
    names = {"ww_auto": "Who&When, auto-built", "ours": "Ours (Free, Critic)", "ww_hand": "Who&When, hand-crafted"}
    fig, ax = plt.subplots(figsize=(7.2, 2.4))
    codes = ["D1", "D2", "D3", "D4", "D5", "reasoning", "tool", "other"]
    y = 0
    ylabels = []
    for src in ("ww_auto", "ours", "ww_hand"):
        sub = df[df.source == src]
        if sub.empty:
            continue
        items = {i: [r] for i, r in enumerate(sub.final)}
        share, lo, hi = cluster_bootstrap(items, lambda xs: sum(x in COMP for x in xs) / len(xs) if xs else float("nan"))
        with_sec = ((sub.final.isin(COMP)) | (sub.secondary.fillna("").isin(COMP))).mean()
        k1 = cohen_kappa(list(sub.coder_a), list(sub.coder_b))
        by = {c: float((sub.final == c).mean()) for c in codes}
        out[src] = {"n": int(len(sub)), "composition_share": share, "ci": [lo, hi], "with_secondary": float(with_sec),
                    "kappa_coders": k1, "agreement": float(sub.agree.mean()), "by_code": by,
                    "d1_d2": by["D1"] + by["D2"], "d4": by["D4"], "reasoning": by["reasoning"],
                    "above_threshold": bool(lo == lo and lo > 0.20)}
        if src.startswith("ww") and "coder_a_annot" in sub:
            out[src]["kappa_after_annotation"] = cohen_kappa(list(sub.coder_a_annot.fillna("")), list(sub.coder_b_annot.fillna("")))
        left = 0.0
        for i, c in enumerate(codes):
            w = by[c]
            ax.barh(y, w, left=left, color=PAL[i], edgecolor="white", linewidth=0.5, label=c if y == 0 else None)
            if w >= 0.06:
                ax.text(left + w / 2, y, f"{100 * w:.0f}", ha="center", va="center", fontsize=7, color="white")
            left += w
        ax.text(1.02, y, f"D1-D5 {100 * share:.1f}% [{pct(lo)}, {pct(hi)}]", va="center", fontsize=7)
        ylabels.append(f"{names[src]} (n={len(sub)})")
        y += 1
    ax.set_yticks(range(len(ylabels)), ylabels, fontsize=8)
    ax.set_xlim(0, 1)
    ax.invert_yaxis()
    ax.set_xlabel("share of coded failures", fontsize=8)
    ax.legend(ncol=8, fontsize=6.5, frameon=False, loc="upper center", bbox_to_anchor=(0.5, 1.28))
    _style(ax)
    _save(fig, "fig_rq1_causes.pdf")
    audit = RESULTS / "rq1" / "audit_whowhen.json"
    if audit.exists():
        out["audit"] = json.loads(audit.read_text())
    S["rq1"] = out


# ==========================================================================
# RQ2
# ==========================================================================

def rq2_mutation() -> None:
    df = _read("rq2_mutants.csv")
    if df.empty:
        return
    m = df[df.operator != "none"]
    groups = [("D1", "Delete a rule", ["delete_rule"]), ("D1", "Detach goal from footprints", ["detach_goal"]),
              ("D2--D5", "Seven others (see caption)", ["break_footprint", "drop_mandatory", "second_writer",
                                                         "duplicate_rule", "add_claim", "drop_clause", "remove_tool"])]
    cols = [f"{v}|{g}" for v in ("path-only", "one-sided", "two-sided") for g in ("G1", "G2")]
    lines = [r"\begin{tabular}{@{}l l r r r r r r r@{}}", r"\toprule",
             r"& & & \multicolumn{2}{c}{Path-only \W4} & \multicolumn{2}{c}{One-sided \W4} & \multicolumn{2}{c}{Two-sided \W4} \\",
             r"\cmidrule(lr){4-5}\cmidrule(lr){6-7}\cmidrule(l){8-9}",
             r"Defect & Mutation operator & $n$ & $G_1$ & $G_2$ & $G_1$ & $G_2$ & $G_1$ & $G_2$ \\", r"\midrule"]
    res = {}
    for d, name, ops in groups:
        sub = m[m.operator.isin(ops)]
        n = len(sub)
        vals = [int(sub[c].sum()) for c in cols]
        res[name] = {"n": n, **dict(zip(cols, vals))}
        cells = [(r"\textbf{%d}" % v) if v == n and n else str(v) for v in vals]
        dl = r"\D1" if d == "D1" else r"\D2--\D5"
        lines.append(f"{dl} & {name} & {n} & " + " & ".join(cells) + r" \\")
    tot = [int(m[c].sum()) for c in cols]
    lines += [r"\midrule", f"& Total & {len(m)} & " + " & ".join((r"\textbf{%d}" % v) if v == len(m) else str(v) for v in tot)
              + r" \\", r"\bottomrule", r"\end{tabular}"]
    TAB.mkdir(parents=True, exist_ok=True)
    (TAB / "tab_mutation.tex").write_text("\n".join(lines) + "\n")
    clean = df[df.operator == "none"]
    S.setdefault("rq2", {})["mutation"] = {"groups": res, "total": dict(zip(cols, tot)), "n": int(len(m)),
                                           "unmutated_rejected": int(clean[cols].sum().sum())}


def rq2_independent() -> None:
    df = _read("rq2_independent.csv")
    if df.empty:
        return
    seeded, clean = df[df.kind == "seeded"], df[df.kind == "clean"]
    det = {}
    dets = ["checker_two_sided", "checker_one_sided"] + [c for c in df.columns if c.startswith("critic:") and not c.endswith(":class_ok")]
    for c in dets:
        k, n = int(seeded[c].sum()), len(seeded)
        fk, fn = int(clean[c].sum()), len(clean)
        det[c] = {"detected": k, "n": n, "rate": k / n if n else float("nan"), "ci": wilson(k, n),
                  "false_alarms": fk, "n_clean": fn, "fa_rate": fk / fn if fn else float("nan"), "fa_ci": wilson(fk, fn),
                  "by_defect": {d: float(seeded[seeded.defect == d][c].mean()) for d in sorted(seeded.defect.dropna().unique())}}
    S.setdefault("rq2", {})["independent"] = det
    S["rq2"]["independent_decision_rule"] = bool(
        det and det["checker_two_sided"]["rate"] > max([v["rate"] for k, v in det.items() if k.startswith("critic:")] or [0])
        and det["checker_two_sided"]["fa_rate"] < min([v["fa_rate"] for k, v in det.items() if k.startswith("critic:")] or [1]))
    fig, axes = plt.subplots(1, 2, figsize=(7.2, 2.3), sharey=True)
    labels = [c.replace("checker_two_sided", "Checker (two-sided)").replace("checker_one_sided", "Checker (one-sided)")
              .replace("critic:", "Critic ") for c in dets]
    for ax, key, title in ((axes[0], "rate", f"Detection of {len(seeded)} seeded defects"),
                           (axes[1], "fa_rate", f"False alarms on {len(clean)} clean teams")):
        vals = [det[c][key] for c in dets]
        cis = [det[c]["ci" if key == "rate" else "fa_ci"] for c in dets]
        ax.barh(range(len(dets)), vals, color=[PAL[0] if c.startswith("checker") else PAL[1] for c in dets])
        ax.errorbar(vals, range(len(dets)), xerr=[[v - lo for v, (lo, hi) in zip(vals, cis)], [hi - v for v, (lo, hi) in zip(vals, cis)]],
                    fmt="none", ecolor="#333", elinewidth=0.8, capsize=2)
        for i, v in enumerate(vals):
            ax.text(min(v + 0.03, 0.9), i, pct(v), va="center", fontsize=7)
        ax.set_xlim(0, 1)
        ax.set_title(title, fontsize=8)
        _style(ax)
    axes[0].set_yticks(range(len(dets)), labels, fontsize=7)
    axes[0].invert_yaxis()
    _save(fig, "fig_independent.pdf")
    nat = RESULTS / "rq2" / "natural_mutants.json"
    if nat.exists():
        S["rq2"]["natural_mutants"] = json.loads(nat.read_text())


def rq2_admission(runs: pd.DataFrame) -> None:
    typed = runs[runs.condition == "autom2m"]
    if typed.empty:
        return
    out = {}
    models = sorted(typed.model.unique())
    fig, ax = plt.subplots(figsize=(6.4, 0.5 + 0.45 * len(models)))
    for yi, m in enumerate(models):
        sub = typed[typed.model == m]
        n = len(sub)
        shares = [float((sub.admitted_round == r).sum()) / n for r in range(4)]
        not_adm = float((sub.admitted == 0).sum()) / n
        out[m] = {"n": n, "admitted": 1 - not_adm, "by_round": shares, "not_admitted": not_adm,
                  "first_round": shares[0],
                  "by_bench": {b: float(sub[sub.bench == b].admitted.mean()) for b in sub.bench.unique()}}
        left = 0
        for r, w in enumerate(shares + [not_adm]):
            ax.barh(yi, w, left=left, color=(PAL[r] if r < 4 else "#bbbbbb"), edgecolor="white",
                    label=(["first proposal", "after 1 round", "after 2 rounds", "after 3 rounds", "not admitted"][r] if yi == 0 else None))
            if w >= 0.06:
                ax.text(left + w / 2, yi, pct(w, 0), ha="center", va="center", fontsize=7, color="white")
            left += w
    ax.set_yticks(range(len(models)), [mlabel(m) for m in models], fontsize=8)
    ax.set_xlim(0, 1)
    ax.set_xlabel("share of AutoM2M sessions", fontsize=8)
    ax.legend(ncol=5, fontsize=6.5, frameon=False, loc="upper center", bbox_to_anchor=(0.5, 1.45))
    _style(ax)
    _save(fig, "fig_admission.pdf")
    adm = _read("admission.csv")
    first = {}
    if not adm.empty:
        rej = adm[(adm.condition == "autom2m") & (adm.rejected == 1)]
        cnt = Counter(rej["first"].fillna("?"))
        tot = sum(cnt.values())
        first = {"rejected_proposals": tot, "unparsable": int(rej.unparsable.sum()),
                 "first_violation": {k: v / tot for k, v in sorted(cnt.items())} if tot else {}}
        first["first_round_admission"] = float(1 - adm[(adm.condition == "autom2m") & (adm["round"] == 0)].rejected.mean())
    # example copying: final teams that copy a worked example's agents, and distinct shapes
    from autom2m.examples import EXAMPLES

    ex_agents = [",".join(sorted(a["name"] for a in t["agents"])) for _task, t, _p in EXAMPLES]
    copying = {}
    for cond in ("autom2m", "autom2m_1ex"):
        sub = runs[(runs.condition == cond) & (runs.bench == "classeval") & (runs.admitted == 1)]
        if sub.empty:
            continue
        shapes = sub.team_shape.fillna("")
        agents = shapes.map(lambda s: s.split("|")[1] if "|" in s else "")
        copying[cond] = {"teams": int(len(sub)), "copies_example": int(agents.isin(ex_agents).sum()),
                         "distinct_shapes": int(shapes.nunique()),
                         "first_round_admission": float((sub.admitted_round == 0).mean())}
    S.setdefault("rq2", {})["admission"] = {"by_model": out, **first, "example_copying": copying}


def rq2_scale(runs: pd.DataFrame) -> None:
    sc = _read("rq2_scale.csv")
    if sc.empty:
        return
    out = {}
    typed = runs[runs.condition.isin(["autom2m", "typed_ref"])]
    builder_ms = float(typed.check_seconds.dropna().div(typed.checks.replace(0, float("nan")).dropna()).median() * 1000) \
        if not typed.empty and typed.checks.notna().any() else float("nan")
    llm_call = runs[runs.condition == "single"].sec_agent.dropna()
    call_s = float(llm_call.median() / 2) if len(llm_call) else 10.0
    fig, ax = plt.subplots(figsize=(3.4, 2.6))
    for i, (topo, mk) in enumerate((("chain", "o"), ("dag", "s"))):
        sub = sc[sc.topology == topo].sort_values("n_views")
        ax.plot(sub.n_views, sub.median_s, marker=mk, ms=3.5, color=PAL[i], label=f"{topo} teams")
        out[topo] = {int(r.n_views): float(r.median_s) for r in sub.itertuples()}
        if {1000, 3000} <= set(sub.n_views):
            a = sub[sub.n_views == 1000].median_s.iloc[0]
            b = sub[sub.n_views == 3000].median_s.iloc[0]
            out[f"{topo}_exponent_1000_3000"] = math.log(b / a) / math.log(3)
    if builder_ms == builder_ms:
        ax.plot([4], [builder_ms / 1000], marker="D", color=PAL[3], ls="none", label="builder teams")
    ax.axhspan(call_s * 0.5, call_s * 1.5, color="#dddddd", alpha=0.6, label="one LLM call")
    ax.set_xscale("log")
    ax.set_yscale("log")
    ax.set_xlabel("views", fontsize=8)
    ax.set_ylabel("checker time (s)", fontsize=8)
    ax.legend(fontsize=6.5, frameon=False)
    ax.tick_params(labelsize=7)
    _save(fig, "fig_scale_a.pdf")
    # (b) per condition at 1000 views
    fig, ax = plt.subplots(figsize=(3.4, 2.2))
    for i, topo in enumerate(("chain", "dag")):
        row = sc[(sc.topology == topo) & (sc.n_views == 1000)]
        if row.empty:
            continue
        left = 0
        for j, c in enumerate(["W1", "W2", "W3", "W4", "W5", "W6"]):
            v = float(row[f"{c}_s"].iloc[0]) * 1000
            ax.barh(i, v, left=left, color=PAL[j], edgecolor="white", label=c if i == 0 else None)
            left += v
        out[f"{topo}_1000_ms"] = {c: float(row[f"{c}_s"].iloc[0]) * 1000 for c in ["W1", "W2", "W3", "W4", "W5", "W6"]}
    ax.set_yticks([0, 1], ["chain", "DAG"], fontsize=8)
    ax.set_xlabel("ms at 1,000 views", fontsize=8)
    ax.legend(ncol=6, fontsize=6.5, frameon=False, loc="upper center", bbox_to_anchor=(0.5, 1.3))
    _style(ax)
    _save(fig, "fig_scale_b.pdf")
    # (c) checks per run, by model group
    groups = model_groups(runs)
    am = runs[(runs.condition == "autom2m") & (runs.bench == "classeval")].copy()
    if not am.empty:
        am["group"] = am.model.map(groups)
        fig, ax = plt.subplots(figsize=(3.4, 2.2))
        for i, g in enumerate(("strong", "weak")):
            sub = am[am.group == g].checks.dropna()
            if sub.empty:
                continue
            cnt = Counter(int(x) for x in sub)
            xs = sorted(cnt)
            ax.bar([x + (i - 0.5) * 0.35 for x in xs], [cnt[x] / len(sub) for x in xs], width=0.35, color=PAL[i], label=g)
            out[f"checks_{g}"] = {"median": float(sub.median()), "mean": float(sub.mean())}
        ax.set_xlabel("checks per run", fontsize=8)
        ax.set_ylabel("share of runs", fontsize=8)
        ax.legend(fontsize=7, frameon=False)
        _style(ax)
        _save(fig, "fig_scale_c.pdf")
        cs = am.check_seconds.dropna() * 1000
        out["check_ms_per_run"] = {"median": float(cs.median()), "p95": float(cs.quantile(0.95))} if len(cs) else {}
    # (d) wall-clock of a run by step (the largest model run on ClassEval)
    if not am.empty:
        m = sorted(am.model.unique())[-1]
        sub = am[(am.model == m) & (am.admitted == 1)]
        steps = {"builder proposal": sub.t_propose, "revision": sub.t_revise[sub.t_revise.fillna(0) > 0],
                 "compilation": sub.t_compile, "checks": sub.check_seconds,
                 "stochastic bindings": (sub.t_run - sub.t_validators.fillna(0)), "validators": sub.t_validators,
                 "attribution": sub.t_attribution[sub.t_attribution.fillna(0) > 0],
                 "repair": sub.t_repair[sub.t_repair.fillna(0) > 0], "whole run": sub.seconds}
        fig, ax = plt.subplots(figsize=(3.4, 2.6))
        d_out = {}
        for i, (k, v) in enumerate(steps.items()):
            v = v.dropna()
            v = v[v > 0]
            if v.empty:
                continue
            q1, med, q3 = v.quantile(0.25), v.median(), v.quantile(0.75)
            ax.plot([q1, q3], [i, i], color=PAL[0], lw=1.5)
            ax.plot([med], [i], marker="o", color=PAL[0], ms=4)
            d_out[k] = {"median_s": float(med), "q1": float(q1), "q3": float(q3), "n": int(len(v))}
        ax.set_xscale("log")
        ax.set_yticks(range(len(steps)), list(steps), fontsize=7)
        ax.invert_yaxis()
        ax.set_xlabel(f"seconds per run ({mlabel(m)}, ClassEval)", fontsize=8)
        _style(ax)
        _save(fig, "fig_scale_d.pdf")
        out["run_steps"] = {"model": m, "admitted_runs": int(len(sub)), **d_out}
        if d_out.get("checks") and d_out.get("whole run"):
            out["check_share_of_run"] = d_out["checks"]["median_s"] / d_out["whole run"]["median_s"]
    S.setdefault("rq2", {})["scale"] = out


# ==========================================================================
# RQ3
# ==========================================================================

def majority(sub: pd.DataFrame) -> dict:
    """task -> majority success over seeds (ties count as failure)."""
    g = sub.groupby("task").success.agg(["sum", "count"])
    return {t: bool(r["sum"] * 2 > r["count"]) for t, r in g.iterrows()}


def rq3_success(runs: pd.DataFrame) -> None:
    out = {}
    for bench in ("classeval", "humanevalplus"):
        rows_tex = []
        b = runs[(runs.bench == bench) & runs.condition.isin(CONDS)]
        if b.empty:
            continue
        means = defaultdict(list)
        n_tests, n_surv = 0, 0
        allp = {}
        for m in sorted(b.model.unique()):
            sm = b[b.model == m]
            maj = {c: majority(sm[sm.condition == c]) for c in CONDS if not sm[sm.condition == c].empty}
            tasks = sorted(set.intersection(*[set(v) for v in maj.values()])) if maj else []
            cq = cochran_q([[maj[c][t] for t in tasks] for c in CONDS if c in maj]) if len(maj) >= 2 and tasks else (float("nan"),) * 2
            succ = {c: float(sm[sm.condition == c].success.mean()) for c in CONDS if c in maj}
            pv, arrows = {}, {}
            if "autom2m" in maj:
                for c in CONDS:
                    if c == "autom2m" or c not in maj:
                        continue
                    a_only, c_only, p = mcnemar([maj["autom2m"][t] for t in tasks], [maj[c][t] for t in tasks])
                    pv[c] = p
                    arrows[c] = "up" if a_only > c_only else "down"
                adj = holm(pv)
                for c, p in adj.items():
                    allp[(m, c)] = pv[c]
                    n_tests += 1
                    n_surv += p < 0.05
            else:
                adj = {}
            best = max(succ.values()) if succ else None
            cells = []
            for c in CONDS:
                if c not in succ:
                    cells.append("--")
                    continue
                cell = pct(succ[c])
                if c in adj and adj[c] < 0.05:
                    cell += r"$^{\uparrow}$" if arrows[c] == "up" else r"$^{\downarrow}$"
                if succ[c] == best:
                    cell = r"\textbf{" + cell + "}"
                cells.append(cell)
                means[c].append(succ[c])
            rows_tex.append(f"{mlabel(m)} & " + " & ".join(cells) + r" \\")
            out.setdefault(bench, {})[m] = {"success": succ, "mcnemar_p": pv, "holm_p": adj, "cochran_q": cq[0],
                                           "cochran_p": cq[1], "tasks": len(tasks),
                                           "seeds": sorted(int(s) for s in sm.seed.unique())}
        mean_cells = [pct(statistics.mean(means[c])) if means[c] else "--" for c in CONDS]
        lines = [r"\begin{tabular}{@{}l rrrrrrrr@{}}", r"\toprule",
                 "Model & " + " & ".join(LAB[c] for c in CONDS) + r" \\", r"\midrule", *rows_tex, r"\midrule",
                 "Mean & " + " & ".join(mean_cells) + r" \\", r"\bottomrule", r"\end{tabular}"]
        TAB.mkdir(parents=True, exist_ok=True)
        (TAB / f"tab_success_{'classeval' if bench == 'classeval' else 'humaneval'}.tex").write_text("\n".join(lines) + "\n")
        out.setdefault(bench, {})["mean"] = {c: statistics.mean(means[c]) for c in CONDS if means[c]}
        out[bench]["holm_within_family_significant"] = n_surv
        out[bench]["tests"] = n_tests
        all_adj = holm({k: v for k, v in allp.items()})
        out[bench]["holm_over_all_significant"] = sum(v < 0.05 for v in all_adj.values())
    # pooled logistic GEE (all runs, clustered by task) and H3b
    rows = runs[runs.condition.isin(CONDS)].copy()
    if not rows.empty and rows.condition.nunique() > 1 and "free" in set(rows.condition):
        rows["cluster"] = rows.bench + "/" + rows.task
        recs = rows[["success", "condition", "cluster", "model", "bench"]].to_dict("records")
        try:
            ors = gee_logit(recs, outcome="success", condition="condition", cluster="cluster", reference="free",
                            extra=["model", "bench"])
        except Exception as exc:  # noqa: BLE001
            ors = {"error": str(exc)}
        out["gee_vs_free"] = ors
        if "autom2m" in ors:
            o, lo, hi, p = ors["autom2m"]
            out["h3b"] = {"or": o, "ci": [lo, hi], "margin": 0.80, "non_inferior": lo > 0.80}
        groups = model_groups(runs)
        rows["group"] = rows.model.map(groups)
        out["gee_by_group"] = {}
        for g in ("strong", "weak"):
            sub = rows[rows.group == g][["success", "condition", "cluster", "model", "bench"]].to_dict("records")
            if sub:
                try:
                    gg = gee_logit(sub, outcome="success", condition="condition", cluster="cluster", reference="free",
                                   extra=["model", "bench"])
                    out["gee_by_group"][g] = gg.get("autom2m")
                except Exception as exc:  # noqa: BLE001
                    out["gee_by_group"][g] = str(exc)
        if rows.model.nunique() > 1:
            try:
                out["interaction_condition_model"] = gee_interaction(
                    rows[["success", "condition", "cluster", "model"]].to_dict("records"), outcome="success",
                    condition="condition", group="model", cluster="cluster")
            except Exception as exc:  # noqa: BLE001
                out["interaction_condition_model"] = str(exc)
    S.setdefault("rq3", {})["success"] = out


def rq3_compfail(runs: pd.DataFrame) -> None:
    codes = _read("rq3_codes.csv")
    if codes.empty:
        return
    groups = model_groups(runs)
    runs = runs[runs.condition.isin(CONDS)].copy()
    runs["key"] = runs.bench + "/" + runs.model.str.replace(":", "_").str.replace("/", "_") + "/" + runs.condition + "/" + \
        runs.task.str.replace("/", "_").str.replace(":", "_") + "__s" + runs.seed.astype(str)
    comp = set(codes[codes.final.isin(COMP)].id)
    runs["comp"] = runs.key.isin(comp).astype(int)
    runs["refused"] = ((runs.condition == "autom2m") & (runs.status == "not_admitted")).astype(int)
    runs["group"] = runs.model.map(groups)

    def rate(sub: pd.DataFrame, coding: str) -> tuple[float, int]:
        if coding == "i":
            sub = sub[sub.refused == 0]
            num = sub.comp.sum()
        elif coding == "ii":
            num = sub.comp.sum() + sub.refused.sum()
        else:
            num = sub.comp.sum()
        return (100 * num / len(sub) if len(sub) else float("nan")), len(sub)

    table = {}
    for c in CONDS:
        cr = runs[runs.condition == c]
        if cr.empty:
            continue
        table[c] = {}
        for coding in ("i", "ii", "iii"):
            for g in ("all", "strong", "weak"):
                sub = cr if g == "all" else cr[cr.group == g]
                table[c][f"{coding}|{g}"] = rate(sub, coding)[0]
    rr = {}
    if "autom2m" in table and "free" in table:
        for coding in ("i", "ii", "iii"):
            for g in ("all", "strong", "weak"):
                a, f = table["autom2m"][f"{coding}|{g}"], table["free"][f"{coding}|{g}"]
                rr[f"{coding}|{g}"] = a / f if f and f == f and a == a else float("nan")
        # bootstrap CI (task-clustered) of the executed-only RR, and vs Critic / Schema
        for other in ("free", "critic", "schema"):
            sub = runs[runs.condition.isin(["autom2m", other])]
            grp = defaultdict(list)
            for r in sub.itertuples():
                grp[(r.bench, r.task)].append((r.condition, r.comp, r.refused))

            def stat(items, other=other):
                a = [x for x in items if x[0] == "autom2m" and x[2] == 0]
                o = [x for x in items if x[0] == other]
                ra = sum(x[1] for x in a) / len(a) if a else float("nan")
                ro = sum(x[1] for x in o) / len(o) if o else float("nan")
                return ra / ro if ro else float("nan")
            rr[f"ci_vs_{other}"] = cluster_bootstrap(grp, stat)
    # ablation: Typed-NC on the sessions AutoM2M admitted
    adm = runs[(runs.condition == "autom2m") & (runs.refused == 0)][["bench", "task", "seed", "model"]]
    nc = runs[runs.condition == "typed_nc"].merge(adm, on=["bench", "task", "seed", "model"])
    table_extra = {"typed_nc_on_admitted": 100 * nc.comp.mean() if len(nc) else float("nan")}
    cols = [f"{c}|{g}" for c in ("i", "ii", "iii") for g in ("all", "strong", "weak")]
    lines = [r"\begin{tabular}{@{}l r r r r r r r r r@{}}", r"\toprule",
             r"& \multicolumn{3}{c}{(i) Executed only} & \multicolumn{3}{c}{(ii) Refused $=$ comp.} & \multicolumn{3}{c}{(iii) Refused $=$ other} \\",
             r"\cmidrule(lr){2-4}\cmidrule(lr){5-7}\cmidrule(l){8-10}",
             r"Condition & All & Strong & Weak & All & Strong & Weak & All & Strong & Weak \\", r"\midrule"]
    for c in ["free", "critic", "schema", "typed_nc", "autom2m", "typed_ref"]:
        if c in table:
            lines.append(f"{LAB[c]} & " + " & ".join("--" if table[c][k] != table[c][k] else f"{table[c][k]:.1f}" for k in cols) + r" \\")
    if rr:
        lines += [r"\midrule", r"RR \auto{}/Free & " + " & ".join("--" if rr[k] != rr[k] else f"{rr[k]:.2f}" for k in cols) + r" \\"]
    lines += [r"\bottomrule", r"\end{tabular}"]
    TAB.mkdir(parents=True, exist_ok=True)
    (TAB / "tab_compfail.tex").write_text("\n".join(lines) + "\n")
    kappa = cohen_kappa(list(codes.coder_a), list(codes.coder_b))
    S.setdefault("rq3", {})["compfail"] = {"per100": table, "rr": rr, **table_extra, "kappa_coders": kappa,
                                           "disagreement": float(1 - codes.agree.mean()), "coded": int(len(codes)),
                                           "h3a_executed": bool(rr and rr.get("i|all", 9) < 1)}


def rq3_done_cost(runs: pd.DataFrame) -> None:
    out = {}
    for bench in ("classeval", "humanevalplus"):
        b = runs[(runs.bench == bench) & runs.condition.isin(CONDS)]
        if b.empty:
            continue
        rows = {}
        am_tok = b[b.condition == "autom2m"].out_tokens.dropna()
        for c in CONDS:
            sub = b[b.condition == c]
            if sub.empty:
                continue
            base = float(sub.success.mean())
            grp = defaultdict(list)
            for r in sub.itertuples():
                grp[r.task].append((r.declared_done, r.success))
            prec = cluster_bootstrap(grp, lambda xs: (sum(s for d, s in xs if d) / sum(d for d, _ in xs)) if sum(d for d, _ in xs) else float("nan"))
            rec = (sub[sub.success == 1].declared_done.mean()) if (sub.success == 1).any() else float("nan")
            p = prec[0]
            f1 = 2 * p * rec / (p + rec) if p == p and rec == rec and (p + rec) else float("nan")
            tok = sub.out_tokens.dropna()
            med_tok = float(tok.median()) if len(tok) else float("nan")
            d = cliffs_delta(list(am_tok), list(tok)) if len(am_tok) and len(tok) and c != "autom2m" else float("nan")
            rows[c] = {"precision": p, "precision_ci": [prec[1], prec[2]], "base_rate": base,
                       "lift": p / base if base else float("nan"), "recall": float(rec), "f1": f1,
                       "median_tokens": med_tok, "median_seconds": float(sub.seconds.median()),
                       "ratio": (float(am_tok.median()) / med_tok) if len(am_tok) and med_tok else float("nan"),
                       "cliff": d, "cliff_mag": cliff_magnitude(d) if d == d else "",
                       "pass_per_mtok": float(sub.success.sum() / tok.sum() * 1e6) if tok.sum() else float("nan"),
                       "declared": float(sub.declared_done.mean())}
            if c in ("free", "critic", "schema"):
                nd = sub[sub.declared_done == 0]
                rows[c]["pass_without_done"] = float(nd.success.mean()) if len(nd) else float("nan")
        if "autom2m" in rows:
            am = b[b.condition == "autom2m"]
            nphi = am[(am.phi == 0) & (am.status != "not_admitted")]
            rows["autom2m"]["false_phi_open_escalation"] = float((nphi.escalations.fillna(0) > 0).mean()) if len(nphi) else float("nan")
            rows["autom2m"]["pass_when_phi_false"] = float(nphi.success.mean()) if len(nphi) else float("nan")
            shares = {r: float(am[f"tok_{r}"].sum()) for r in ("builder", "binding", "attribution", "repair")}
            tot = sum(shares.values())
            rows["autom2m"]["token_shares"] = {k: v / tot for k, v in shares.items()} if tot else {}
        out[bench] = rows
        if bench != "classeval":
            continue
        lines = [r"\begin{tabular}{@{}l r r r r r r r r r@{}}", r"\toprule",
                 r"& \multicolumn{4}{c}{Reliability of ``done''} & \multicolumn{5}{c}{Cost} \\",
                 r"\cmidrule(lr){2-5}\cmidrule(l){6-10}",
                 r"Condition & Prec. [95\% CI] & Lift & Rec. & $F_1$ & Tok.\ (k) & Sec. & Ratio & $\delta$ & Pass/Mtok \\", r"\midrule"]
        for c in CONDS:
            if c not in rows:
                continue
            r = rows[c]
            ci = r["precision_ci"]
            d = "--" if c == "autom2m" or r["cliff"] != r["cliff"] else f"{r['cliff']:+.2f} ({r['cliff_mag']})"
            ratio = "--" if c == "autom2m" else f"{r['ratio']:.1f}"
            lines.append(f"{LAB[c]} & {pct(r['precision'])} [{pct(ci[0], 0)}, {pct(ci[1], 0)}] & {r['lift']:.2f} & "
                         f"{pct(r['recall'])} & {pct(r['f1'])} & {r['median_tokens'] / 1000:.1f} & {r['median_seconds']:.0f} & "
                         f"{ratio} & {d} & {r['pass_per_mtok']:.1f}" + r" \\")
        lines += [r"\bottomrule", r"\end{tabular}"]
        TAB.mkdir(parents=True, exist_ok=True)
        (TAB / "tab_donecost.tex").write_text("\n".join(lines) + "\n")
    S.setdefault("rq3", {})["done_cost"] = out


# ==========================================================================
# RQ4
# ==========================================================================

def rq4() -> None:
    inj = _read("rq4_inject.csv")
    out = {}
    classes = ["upstream", "footprint", "specification", "sampling", "validator"]
    if not inj.empty:
        for team in sorted(inj.team.dropna().unique()):
            sub = inj[(inj.team == team) & (inj.manifested == 1)]
            if sub.empty:
                continue
            res = {"n": int(len(sub)), "per_fault": {f: int((sub.fault == f).sum()) for f in classes},
                   "symptom_located": float(sub.symptom_ok.mean()), "responsible_found": float(sub.responsible_ok.mean()),
                   "responsible_found_upstream": float(sub[sub.fault == "upstream"].responsible_ok.mean())
                   if (sub.fault == "upstream").any() else float("nan"),
                   "agent_ok": float(sub.agent_ok.mean())}
            for n in (1, 3):
                acc = {f: float((sub[sub.fault == f][f"pred_class_n{n}"] == f).mean()) for f in classes if (sub.fault == f).any()}
                res[f"accuracy_n{n}"] = acc
                res[f"macro_n{n}"] = statistics.mean(acc.values()) if acc else float("nan")
                res[f"calls_n{n}"] = float(sub[f"calls_n{n}"].mean())
            a1 = [x == f for x, f in zip(sub.pred_class_n1, sub.fault)]
            a3 = [x == f for x, f in zip(sub.pred_class_n3, sub.fault)]
            res["mcnemar_adaptive_vs_1"] = mcnemar(a3, a1)
            res["confusion_adaptive"] = {f: dict(Counter(sub[sub.fault == f].pred_class_n3.fillna("?"))) for f in classes}
            out[team] = res
    tr = _read("rq4_transcript.csv")
    if not tr.empty:
        meth = {}
        for m in ("all_at_once", "step_by_step", "binary_search", "agentic_replay"):
            if f"{m}_agent" not in tr:
                continue
            sub = tr[tr.team == "ref"] if (tr.team == "ref").any() else tr
            acc = {f: float(sub[sub.fault == f][f"{m}_class"].mean()) for f in classes if (sub.fault == f).any()}
            meth[m] = {"agent": float(sub[f"{m}_agent"].mean()), "step": float(sub[f"{m}_step"].mean()),
                       "class": float(sub[f"{m}_class"].mean()), "macro_class": statistics.mean(acc.values()) if acc else float("nan"),
                       "calls": float(sub[f"{m}_calls"].mean())}
        out["transcript_methods"] = meth
        best = max((v["macro_class"] for v in meth.values()), default=float("nan"))
        if "ref" in out:
            out["decision_rule"] = bool(out["ref"]["macro_n3"] > best)
    nat = _read("rq4_natural.csv")
    if not nat.empty:
        out["natural"] = {"n": int(len(nat)), "agent": float(nat.agent_ok.mean()), "binding": float(nat.binding_ok.mean()),
                          "coders_agree": float(nat.coders_agree.mean())}
    rep = _read("rq4_repair.csv")
    if not rep.empty:
        k_r, k_b, n = int(rep.repair_success.sum()), int(rep.rebuild_success.sum()), len(rep)
        diff = paired_bootstrap_diff(list(rep.repair_success), list(rep.rebuild_success), b=2000)
        out["repair"] = {"n": n, "repair_success": k_r / n, "repair_ci": wilson(k_r, n), "rebuild_success": k_b / n,
                         "rebuild_ci": wilson(k_b, n), "diff": diff, "mcnemar": mcnemar(list(rep.repair_success == 1), list(rep.rebuild_success == 1)),
                         "repair_tokens_median": float(rep.repair_tokens.median()), "rebuild_tokens_median": float(rep.rebuild_tokens.median()),
                         "preserved_mean": float(rep.preserved.dropna().mean()) if rep.preserved.notna().any() else float("nan"),
                         "modes": {k: v / n for k, v in Counter(rep["mode"].fillna("none")).items()}}
    S["rq4"] = out
    if out.get("ref") or out.get("transcript_methods"):
        fig, axes = plt.subplots(1, 2, figsize=(7.2, 2.6), gridspec_kw={"width_ratios": [1.3, 1]})
        ax = axes[0]
        bars = []
        if "ref" in out:
            r = out["ref"]
            bars.append(("AutoM2M (1 replay)", r["agent_ok"], r["responsible_found"], r["macro_n1"]))
            bars.append(("AutoM2M (adaptive)", r["agent_ok"], r["responsible_found"], r["macro_n3"]))
        for m, v in (out.get("transcript_methods") or {}).items():
            bars.append((m.replace("_", " "), v["agent"], v["step"], v["macro_class"]))
        for j, (lab_, *vals) in enumerate(bars):
            for k, v in enumerate(vals):
                ax.bar(j + (k - 1) * 0.26, v, width=0.26, color=PAL[k], label=["agent", "step / binding", "fault class"][k] if j == 0 else None)
        ax.set_xticks(range(len(bars)), [b[0] for b in bars], rotation=30, ha="right", fontsize=7)
        ax.set_ylim(0, 1)
        ax.set_ylabel("accuracy", fontsize=8)
        ax.legend(fontsize=6.5, frameon=False)
        ax.spines["top"].set_visible(False)
        ax.spines["right"].set_visible(False)
        ax = axes[1]
        if "ref" in out:
            conf = out["ref"]["confusion_adaptive"]
            mat = [[conf.get(t, {}).get(p, 0) for p in classes] for t in classes]
            ax.imshow(mat, cmap="Blues")
            for i in range(5):
                for j in range(5):
                    if mat[i][j]:
                        ax.text(j, i, mat[i][j], ha="center", va="center", fontsize=7,
                                color="white" if mat[i][j] > max(max(r) for r in mat) / 2 else "black")
            ax.set_xticks(range(5), [c[:5] for c in classes], fontsize=7, rotation=30)
            ax.set_yticks(range(5), [c[:5] for c in classes], fontsize=7)
            ax.set_xlabel("predicted", fontsize=8)
            ax.set_ylabel("injected", fontsize=8)
        _save(fig, "fig_attribution.pdf")


def _clean(o):
    """NaN -> null, tuple keys -> strings, numpy scalars -> Python."""
    if isinstance(o, dict):
        return {(k if isinstance(k, str) else "|".join(map(str, k)) if isinstance(k, tuple) else str(k)): _clean(v)
                for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_clean(v) for v in o]
    if hasattr(o, "item") and not isinstance(o, (str, bytes)):
        try:
            o = o.item()
        except Exception:  # noqa: BLE001
            pass
    if isinstance(o, float) and o != o:
        return None
    return o


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-export", action="store_true")
    a = ap.parse_args(argv)
    if not a.no_export:
        from .export import main as export

        export()
    runs = _read("runs.csv")
    rq1()
    rq2_mutation()
    rq2_independent()
    if not runs.empty:
        rq2_admission(runs)
        rq2_scale(runs)
        rq3_success(runs)
        rq3_compfail(runs)
        rq3_done_cost(runs)
        S["runs"] = {"n": int(len(runs)), "by_condition": runs.groupby(["bench", "condition"]).size().unstack(fill_value=0).to_dict()}
    else:
        rq2_scale(pd.DataFrame(columns=["condition", "check_seconds", "checks", "sec_agent", "bench", "model"]))
    rq4()
    out = RESULTS / "summary.json"
    out.write_text(json.dumps(_clean(S), indent=1, default=str))
    print("wrote", out, "and", TAB, FIG)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
