"""Flatten the raw logs into the analysis tables results/data/*.csv.

    python -m evaluation.analysis.export

  runs.csv            one row per run (all conditions, benchmarks, models, seeds)
  rq1_codes.csv       RQ1 decisive (and secondary) causes, both coders
  rq2_mutants.csv     mutation analysis (three W4 variants x G1/G2)
  rq2_independent.csv seeded defects and clean teams: checker variants and critics
  rq2_scale.csv       checker time on synthetic teams
  rq3_codes.csv       decisive causes of failing executed runs (all conditions)
  rq4_inject.csv      injected faults: truth, trace attribution under each budget
  rq4_transcript.csv  transcript-based methods on the same injected runs
  rq4_natural.csv     lookup vs coders on natural AutoM2M failures
  rq4_repair.csv      checked repair vs rebuild
"""
from __future__ import annotations

import csv
import json
from pathlib import Path

import os

ROOT = Path(__file__).resolve().parents[2]
RES = Path(os.environ.get("AM2M_RESULTS", ROOT / "results"))
DATA = RES / "data"
ROLES = ("builder", "binding", "attribution", "repair", "agent", "critic")


def _jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    out = []
    for line in path.read_text().splitlines():
        line = line.strip()
        if line:
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return out


def _write(name: str, rows: list[dict]) -> Path:
    DATA.mkdir(parents=True, exist_ok=True)
    p = DATA / name
    keys: list[str] = []
    for r in rows:
        for k in r:
            if k not in keys:
                keys.append(k)
    with p.open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=keys)
        w.writeheader()
        for r in rows:
            w.writerow({k: (json.dumps(v) if isinstance(v, (list, dict)) else v) for k, v in r.items()})
    return p


def runs() -> list[dict]:
    seen: dict = {}
    for f in sorted((RES / "raw").glob("*.jsonl")):
        for r in _jsonl(f):
            if r.get("status") == "error":
                continue
            seen[(r["bench"], r["model"], r["task"], r["condition"], r["seed"])] = r
    rows = []
    for r in seen.values():
        tok = r.get("tokens") or {}
        timing = r.get("timing") or {}
        row = {
            "bench": r["bench"], "model": r["model"], "task": r["task"], "condition": r["condition"], "seed": r["seed"],
            "status": r["status"], "success": int(bool(r.get("success"))), "test_pass_rate": r.get("test_pass_rate"),
            "declared_done": int(bool(r.get("declared_done"))), "seconds": r.get("seconds"),
            "out_tokens": (r.get("total") or {}).get("out_tokens"), "in_tokens": (r.get("total") or {}).get("in_tokens"),
            "calls": (r.get("total") or {}).get("calls"), "budget_hit": int(bool(r.get("budget_hit"))),
        }
        for role in ROLES:
            row[f"tok_{role}"] = (tok.get(role) or {}).get("out_tokens", 0)
            row[f"sec_{role}"] = (tok.get(role) or {}).get("seconds", 0)
        if r["condition"] in ("typed_nc", "autom2m", "typed_ref", "autom2m_1ex"):
            row.update(admitted=int(bool(r.get("admitted"))), admitted_round=r.get("admitted_round"),
                       admission_rounds=r.get("admission_rounds"), first_violation=r.get("first_violation"),
                       unparsable=r.get("unparsable"), checks=r.get("checks"), check_seconds=r.get("check_seconds"),
                       phi=int(bool(r.get("phi"))), escalations=r.get("escalations"), repairs=r.get("repairs"),
                       repair_sources=r.get("repair_sources"), builder_unchanged=r.get("builder_unchanged"),
                       fault_classes=r.get("fault_classes"), attribution_calls=r.get("attribution_calls"),
                       team_shape=r.get("team_shape"), first_shape=r.get("first_shape"), first_diags=r.get("first_diags"),
                       unchecked_violations=r.get("unchecked_violations"),
                       t_propose=timing.get("propose"), t_revise=timing.get("revise"), t_compile=timing.get("compile"),
                       t_run=timing.get("run"), t_validators=timing.get("validators"),
                       t_attribution=timing.get("attribution"), t_repair=timing.get("repair"), t_delta=timing.get("delta"))
        else:
            row.update(terminated_by=r.get("terminated_by"), candidates=r.get("candidates"), turns=r.get("turns"))
        rows.append(row)
    return rows


def admission_diags() -> list[dict]:
    """Every proposal's diagnostics (all admission rounds) of the typed runs."""
    rows = []
    for f in sorted((RES / "runs").glob("*/*/*/*.json")):
        cond = f.parent.name
        if cond not in ("autom2m", "autom2m_1ex"):
            continue
        d = json.loads(f.read_text())
        det = d.get("detail") or {}
        for i, diags in enumerate(det.get("diagnostics") or []):
            rows.append({"bench": d["bench"], "model": d["model"], "task": d["task"], "seed": d["seed"], "condition": cond,
                         "round": i, "rejected": int(bool(diags)), "first": (diags[0][:2] if diags else None),
                         "unparsable": int(bool(diags) and "not a JSON object" in diags[0]),
                         "conds": sorted({x[:2] for x in diags})})
    return rows


def main() -> int:
    r = runs()
    print("runs.csv", len(r), _write("runs.csv", r))
    print("admission.csv", _write("admission.csv", admission_diags()))
    rq1 = []
    for src in ("whowhen", "ours"):
        for x in _jsonl(RES / "rq1" / f"codes_{src}.jsonl"):
            rq1.append({"id": x["id"], "source": x["source"], "condition": x.get("condition"),
                        "coder_a": x["coder_a"]["code"], "coder_b": x["coder_b"]["code"],
                        "coder_a_annot": (x.get("coder_a_annot") or {}).get("code"),
                        "coder_b_annot": (x.get("coder_b_annot") or {}).get("code"),
                        "final": x["final"], "secondary": x.get("secondary"), "agree": int(bool(x.get("agree")))})
    print("rq1_codes.csv", len(rq1), _write("rq1_codes.csv", rq1))
    mut = []
    for x in _jsonl(RES / "rq2" / "mutants.jsonl"):
        row = {"team": x["team"], "operator": x["operator"], "defect": x["defect"], "site": x["site"]}
        for k, v in x["checks"].items():
            row[k] = int(v["rejected"])
        mut.append(row)
    print("rq2_mutants.csv", len(mut), _write("rq2_mutants.csv", mut))
    ind = []
    for x in _jsonl(RES / "rq2" / "independent.jsonl"):
        row = {"kind": x["kind"], "defect": x.get("defect"), "source": x.get("source"),
               "checker_two_sided": int(x["checker_two-sided"]), "checker_one_sided": int(x["checker_one-sided"]),
               "checker_path_only": int(x["checker_path-only"]), "conds": x.get("conds")}
        for k, v in x.items():
            if k.startswith("critic:"):
                row[k] = int(v["flagged"])
                row[k + ":class_ok"] = int(x.get("defect") in v["classes"]) if x.get("defect") else None
        ind.append(row)
    print("rq2_independent.csv", len(ind), _write("rq2_independent.csv", ind))
    scale = json.loads((RES / "rq2" / "scale.json").read_text()) if (RES / "rq2" / "scale.json").exists() else []
    sc = [{"topology": x["topology"], "n_views": x["n_views"], "median_s": x["median_s"], "min_s": x["min_s"],
           "max_s": x["max_s"], **{f"{c}_s": v for c, v in x["cond_median_s"].items()}} for x in scale]
    print("rq2_scale.csv", len(sc), _write("rq2_scale.csv", sc))
    rq3 = [{"id": x["id"], "bench": x["bench"], "model": x["model"], "condition": x["condition"],
            "task": x["id"].split("/")[-1].rsplit("__s", 1)[0], "seed": int(x["id"].rsplit("__s", 1)[1]),
            "coder_a": x["coder_a"]["code"], "coder_b": x["coder_b"]["code"], "final": x["final"],
            "secondary": x.get("secondary"), "agree": int(bool(x.get("agree")))}
           for x in _jsonl(RES / "rq3" / "codes_runs.jsonl")]
    print("rq3_codes.csv", len(rq3), _write("rq3_codes.csv", rq3))
    inj = []
    for f in sorted((RES / "rq4").glob("inject_*.jsonl")):
        for x in _jsonl(f):
            if not x.get("fault") or "error" in x:
                continue
            t = x.get("truth") or {}
            rep = x.get("report") or {}
            resp = x.get("responsible") or {}
            sym = x.get("symptom") or {}
            inj.append({"team": x.get("team"), "model": x.get("model"), "task": x["task"], "fault": x["fault"],
                        "manifested": int(bool(x.get("manifested"))), "truth_agent": t.get("agent"),
                        "truth_binding": t.get("binding"), "pred_class_n1": x.get("class_n1"),
                        "pred_class_n2": x.get("class_n2"), "pred_class_n3": x.get("class_n3"),
                        "calls_n1": x.get("calls_n1"), "calls_n3": x.get("calls_n3"),
                        "exhaustive_calls": x.get("exhaustive_calls"),
                        "symptom_ok": int((rep.get("rule"), rep.get("binding"), rep.get("target_key")) ==
                                          (sym.get("rule"), sym.get("binding"), sym.get("target"))) if x.get("manifested") else None,
                        "responsible_binding": f"{resp.get('rule')}.{resp.get('binding')}" if resp else None,
                        "responsible_ok": int(f"{resp.get('rule')}.{resp.get('binding')}" == t.get("binding") and
                                              (not t.get("target") or resp.get("target_key") == t.get("target")))
                        if x.get("manifested") else None,
                        "agent_ok": int(resp.get("agent") == t.get("agent") or (t.get("agent") == "(validator)" and
                                                                                 rep.get("fault_class") == "validator"))
                        if x.get("manifested") else None})
    print("rq4_inject.csv", len(inj), _write("rq4_inject.csv", inj))
    tr = []
    for f in sorted((RES / "rq4").glob("transcript_*.jsonl")):
        for x in _jsonl(f):
            row = {"team": x.get("team"), "task": x["task"], "fault": x["fault"], "n_steps": x["n_steps"]}
            for m in ("all_at_once", "step_by_step", "binary_search", "agentic_replay"):
                if m in x:
                    row.update({f"{m}_agent": int(x[m]["agent_ok"]), f"{m}_step": int(x[m]["step_ok"]),
                                f"{m}_class": int(x[m]["class_ok"]), f"{m}_calls": x[m].get("calls"),
                                f"{m}_pred": x[m].get("pred_class")})
            tr.append(row)
    print("rq4_transcript.csv", len(tr), _write("rq4_transcript.csv", tr))
    nat = [{"run": x["run"], "bench": x["bench"], "fault_class": x["fault_class"], "agent_ok": int(x["agent_ok"]),
            "binding_ok": int(x["binding_ok"]), "coders_agree": int(x["coders_agree"])}
           for x in _jsonl(RES / "rq4" / "natural.jsonl")]
    print("rq4_natural.csv", len(nat), _write("rq4_natural.csv", nat))
    rep = []
    for f in sorted((RES / "rq4").glob("repair__*.jsonl")):
        for x in _jsonl(f):
            if "repair" not in x or "rebuild" not in x:
                continue
            info = x["repair"].get("info") or {}
            rep.append({"bench": x["bench"], "task": x["task"], "seed": x["seed"],
                        "repair_success": int(bool(x["repair"].get("success"))), "repair_phi": int(bool(x["repair"].get("phi"))),
                        "repair_tokens": x["repair"].get("out_tokens"), "preserved": x["repair"].get("preserved"),
                        "mode": info.get("mode"), "source": info.get("source"), "admitted": int(bool(info.get("checked"))),
                        "builder_unchanged": info.get("builder_unchanged"),
                        "rebuild_success": int(bool(x["rebuild"].get("success"))), "rebuild_tokens": x["rebuild"].get("out_tokens"),
                        "rebuild_admitted": int(bool(x["rebuild"].get("admitted")))})
    print("rq4_repair.csv", len(rep), _write("rq4_repair.csv", rep))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
