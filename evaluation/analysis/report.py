"""Write docs/results-qwen7b.md (or another model's report) from results/summary.json.

    python -m evaluation.analysis.report
"""
from __future__ import annotations

import datetime as dt
import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RESULTS = Path(os.environ.get("AM2M_RESULTS", ROOT / "results"))
OUT = Path(os.environ.get("AM2M_REPORT", ROOT / "docs" / "results-qwen7b.md"))
CONDS = ["single", "single_gate", "free", "critic", "schema", "typed_nc", "autom2m", "typed_ref"]
LAB = {"single": "Single", "single_gate": "Single-Gate", "free": "Free", "critic": "Critic", "schema": "Schema",
       "typed_nc": "Typed-NC", "autom2m": "AutoM2M", "typed_ref": "Typed-Ref"}


def p(x, d=1):
    return "—" if not isinstance(x, (int, float)) else f"{100 * x:.{d}f}%"


def n(x, d=1):
    return "—" if not isinstance(x, (int, float)) else f"{x:.{d}f}"


def g(d, *ks):
    for k in ks:
        if not isinstance(d, dict) or k not in d:
            return None
        d = d[k]
    return d


def main() -> int:
    s = json.loads((RESULTS / "summary.json").read_text())
    L = [f"# Results of the real runs\n",
         f"Generated {dt.date.today().isoformat()} by `python -m evaluation.analysis.report` from "
         "`results/summary.json`. Every number comes from the logs in `results/`; nothing is simulated. "
         "The paper's Sec. 4 numbers are partly synthetic placeholders (see its notice); compare with care.\n"]
    runs = g(s, "runs", "by_condition") or {}
    L.append("## Runs\n")
    L.append("| condition | " + " | ".join(sorted({b for v in runs.values() for b in v})) + " |")
    benches = sorted({b for v in runs.values() for b in v})
    L.append("|---|" + "---|" * len(benches))
    for c in CONDS + ["autom2m_1ex"]:
        if c in runs:
            L.append(f"| {LAB.get(c, c)} | " + " | ".join(str(runs[c].get(b, 0)) for b in benches) + " |")
    L.append("")
    # RQ1
    L.append("## RQ1: decisive causes of failures\n")
    rq1 = g(s, "rq1") or {}
    names = {"ww_auto": "Who&When, auto-built", "ours": "Ours (Free, Critic)", "ww_hand": "Who&When, hand-crafted"}
    if any(k in rq1 for k in names):
        L += ["| source | n | D1-D5 decisive [95% CI] | incl. secondary | D1+D2 | D4 | reasoning | κ |", "|---|---|---|---|---|---|---|---|"]
        for k, nm in names.items():
            v = rq1.get(k)
            if v:
                ci = v.get("ci") or [None, None]
                L.append(f"| {nm} | {v['n']} | {p(v['composition_share'])} [{p(ci[0], 0)}, {p(ci[1], 0)}] | {p(v['with_secondary'])} | "
                         f"{p(v['d1_d2'])} | {p(v['d4'])} | {p(v['reasoning'])} | {n(v.get('kappa_coders'), 2)} |")
        L.append("\nDecision rule (lower CI bound above 20% for LLM-assembled teams): "
                 + ", ".join(f"{names[k]}: {'met' if rq1[k].get('above_threshold') else 'not met'}" for k in ("ww_auto", "ours") if k in rq1) + ".\n")
    else:
        L.append("Pending.\n")
    a = g(rq1, "audit", "algorithm_generated")
    if a:
        L.append(f"Audit of the 126 CaptainAgent teams: {a['roles_naming_teammate']}/{a['roles']} roles name a teammate, "
                 f"{a['roles_stating_output']}/{a['roles']} state their output, {a['plans_assigning_step_to_role']}/{a['teams']} "
                 f"plans assign a step to a role, {a['runs_terminated_by_agent']}/{a['teams']} failed runs ended with TERMINATE.\n")
    # RQ2
    L.append("## RQ2: prevention\n")
    m = g(s, "rq2", "mutation")
    if m:
        L += ["| mutation operator | n | path-only G1/G2 | one-sided G1/G2 | two-sided G1/G2 |", "|---|---|---|---|---|"]
        for k, v in m["groups"].items():
            L.append(f"| {k} | {v['n']} | {v['path-only|G1']}/{v['path-only|G2']} | {v['one-sided|G1']}/{v['one-sided|G2']} | "
                     f"{v['two-sided|G1']}/{v['two-sided|G2']} |")
        t = m["total"]
        L.append(f"| total | {m['n']} | {t['path-only|G1']}/{t['path-only|G2']} | {t['one-sided|G1']}/{t['one-sided|G2']} | "
                 f"{t['two-sided|G1']}/{t['two-sided|G2']} |\n")
    ind = g(s, "rq2", "independent")
    if ind:
        L += ["| detector | seeded defects detected | false alarms on clean teams |", "|---|---|---|"]
        for k, v in ind.items():
            L.append(f"| {k} | {v['detected']}/{v['n']} ({p(v['rate'])}) | {v['false_alarms']}/{v['n_clean']} ({p(v['fa_rate'])}) |")
        L.append(f"\nDecision rule (checker beats the best critic on detection and false alarms): "
                 f"{'met' if g(s, 'rq2', 'independent_decision_rule') else 'not met'}.\n")
    nat = g(s, "rq2", "natural_mutants", "operators", "detach_goal")
    if nat:
        L.append(f"Detach on {nat.get('n')} builder-generated admitted teams: caught by two-sided W4 {nat.get('two-sided')}, "
                 f"one-sided {nat.get('one-sided')}, path-only {nat.get('path-only')}.\n")
    adm = g(s, "rq2", "admission")
    if adm:
        for model, v in (adm.get("by_model") or {}).items():
            L.append(f"Admission ({model}): {p(v['admitted'])} of {v['n']} AutoM2M sessions admitted, {p(v['first_round'])} at the "
                     f"first proposal; by round {', '.join(p(x) for x in v['by_round'])}.")
        if adm.get("first_violation"):
            L.append(f"First violation of the {adm.get('rejected_proposals')} rejected proposals: "
                     + ", ".join(f"{k} {p(x)}" for k, x in adm["first_violation"].items()) + f" ({adm.get('unparsable')} not valid JSON).")
        for c, v in (adm.get("example_copying") or {}).items():
            L.append(f"Example copying ({c}): {v['copies_example']}/{v['teams']} admitted ClassEval teams copy a worked example's "
                     f"agents; {v['distinct_shapes']} distinct shapes; first-round admission {p(v['first_round_admission'])}.")
        L.append("")
    sc = g(s, "rq2", "scale")
    if sc:
        L.append(f"Checker cost: chain teams {', '.join(f'{k} views {n(1000 * v, 1)} ms' for k, v in (sc.get('chain') or {}).items())}; "
                 f"local exponent 1,000→3,000 views {n(sc.get('chain_exponent_1000_3000'), 2)} (chain), "
                 f"{n(sc.get('dag_exponent_1000_3000'), 2)} (DAG). Checks per run: {sc.get('checks_strong')}; "
                 f"checking time per run {sc.get('check_ms_per_run')} ms; share of a run {n(sc.get('check_share_of_run'), 8)}.\n")
    # RQ3
    L.append("## RQ3: effect\n")
    su = g(s, "rq3", "success") or {}
    for b in ("classeval", "humanevalplus"):
        mean = g(su, b, "mean")
        if not mean:
            continue
        L += [f"**Success on {b}** (mean over seeds):\n", "| " + " | ".join(LAB[c] for c in CONDS) + " |", "|" + "---|" * len(CONDS),
              "| " + " | ".join(p(mean.get(c)) for c in CONDS) + " |\n"]
        for model, v in su[b].items():
            if isinstance(v, dict) and "holm_p" in v:
                sig = [f"{LAB[c]} (p={n(x, 3)})" for c, x in v["holm_p"].items() if x < 0.05]
                L.append(f"{model}: Cochran's Q p = {n(v.get('cochran_p'), 3)}; AutoM2M differs significantly (Holm) from: "
                         f"{', '.join(sig) or 'none'}; seeds {v.get('seeds')}.\n")
    gee = su.get("gee_vs_free")
    if isinstance(gee, dict) and gee and "error" not in gee:
        L.append("Pooled logistic GEE, odds ratios against Free: " + ", ".join(f"{LAB.get(k, k)} {n(v[0], 2)} [{n(v[1], 2)}, {n(v[2], 2)}]" for k, v in gee.items()) + ".")
    h = su.get("h3b")
    if h:
        L.append(f"H3b (non-inferiority, margin OR 0.80): OR {n(h['or'], 2)} [{n(h['ci'][0], 2)}, {n(h['ci'][1], 2)}] → "
                 f"{'holds' if h['non_inferior'] else 'not shown'}.\n")
    cf = g(s, "rq3", "compfail")
    if cf:
        L += ["**Composition-caused failures per 100 runs** (codings of refused sessions):\n",
              "| condition | (i) executed only | (ii) refused = composition | (iii) refused = other |", "|---|---|---|---|"]
        for c in CONDS:
            v = cf["per100"].get(c)
            if v:
                L.append(f"| {LAB[c]} | {n(v['i|all'])} | {n(v['ii|all'])} | {n(v['iii|all'])} |")
        rr = cf.get("rr") or {}
        L.append(f"\nRisk ratio AutoM2M/Free (executed only): {n(rr.get('i|all'), 2)}, CI {rr.get('ci_vs_free')}; H3a "
                 f"{'holds' if cf.get('h3a_executed') else 'not shown'} for executed teams. Typed-NC on the sessions AutoM2M admitted: "
                 f"{n(cf.get('typed_nc_on_admitted'))}. Coder agreement κ = {n(cf.get('kappa_coders'), 2)} over {cf.get('coded')} coded failures.\n")
    dn = g(s, "rq3", "done_cost") or {}
    for b, rows in dn.items():
        L += [f"**Reliability of \"done\" and cost on {b}:**\n",
              "| condition | precision [95% CI] | lift | recall | F1 | median tokens | median s | Pass/Mtok |", "|---|---|---|---|---|---|---|---|"]
        for c in CONDS:
            v = rows.get(c)
            if v:
                ci = v.get("precision_ci") or [None, None]
                L.append(f"| {LAB[c]} | {p(v['precision'])} [{p(ci[0], 0)}, {p(ci[1], 0)}] | {n(v['lift'], 2)} | {p(v['recall'])} | "
                         f"{p(v['f1'])} | {n(v['median_tokens'], 0)} | {n(v['median_seconds'], 0)} | {n(v['pass_per_mtok'])} |")
        am = rows.get("autom2m") or {}
        if am.get("token_shares"):
            L.append("\nAutoM2M token shares: " + ", ".join(f"{k} {p(x)}" for k, x in am["token_shares"].items())
                     + f"; runs with false φ ending in an open escalation {p(am.get('false_phi_open_escalation'))}; "
                     f"pass rate when φ is false {p(am.get('pass_when_phi_false'))}.")
        L.append("")
    # RQ4
    L.append("## RQ4: attribution and repair\n")
    r4 = g(s, "rq4") or {}
    for team in ("ref", "builder"):
        v = r4.get(team)
        if v:
            L.append(f"**{'Reference' if team == 'ref' else 'Builder-generated'} teams**, {v['n']} manifested injected faults "
                     f"({v['per_fault']}): symptom located {p(v['symptom_located'])}, responsible binding found {p(v['responsible_found'])} "
                     f"(upstream {p(v['responsible_found_upstream'])}), agent {p(v['agent_ok'])}; fault-class macro accuracy "
                     f"{p(v['macro_n1'])} with one replay ({n(v['calls_n1'])} calls) and {p(v['macro_n3'])} adaptive ({n(v['calls_n3'])} calls); "
                     f"per class adaptive {', '.join(f'{k} {p(x)}' for k, x in v['accuracy_n3'].items())}; McNemar adaptive vs one replay {v['mcnemar_adaptive_vs_1']}.\n")
    tm = r4.get("transcript_methods")
    if tm:
        L += ["| transcript method | agent | step | fault class (macro) | calls |", "|---|---|---|---|---|"]
        for k, v in tm.items():
            L.append(f"| {k} | {p(v['agent'])} | {p(v['step'])} | {p(v['macro_class'])} | {n(v['calls'])} |")
        L.append(f"\nDecision rule (trace attribution beats the best transcript method on fault class): "
                 f"{'met' if r4.get('decision_rule') else 'not met'}.\n")
    if r4.get("natural"):
        v = r4["natural"]
        L.append(f"Natural failures ({v['n']}): lookup agrees with the coders on the agent in {p(v['agent'])} and the binding in {p(v['binding'])}.\n")
    if r4.get("repair"):
        v = r4["repair"]
        L.append(f"Repair vs rebuild ({v['n']} failed AutoM2M runs): one checked delta passes {p(v['repair_success'])}, rebuilding "
                 f"{p(v['rebuild_success'])}; difference {v['diff']}; McNemar {v['mcnemar']}; median tokens {n(v['repair_tokens_median'], 0)} "
                 f"vs {n(v['rebuild_tokens_median'], 0)}; preserved values {p(v['preserved_mean'])}; modes {v['modes']}.\n")
    if not r4:
        L.append("Pending.\n")
    OUT.write_text("\n".join(L) + "\n")
    print("wrote", OUT)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
