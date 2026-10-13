"""hpc/scripts/audit.py — colour audit of a pipeline run: jobs (nodes) > steps > substeps.

    python3 hpc/scripts/audit.py [RUN_ID]            one snapshot (default: newest run in hpc/logs/)
    python3 hpc/scripts/audit.py RUN_ID --watch      live table, redrawn every 60 s (--watch 120 for 2 min; minimum 60);
                                                     ends by itself when no job is pending or running
    python3 hpc/scripts/audit.py RUN_ID --no-color   plain text (also automatic when not a terminal / NO_COLOR set)
    python3 hpc/scripts/audit.py RUN_ID --only failed    show only jobs that failed / were cancelled
    python3 hpc/scripts/audit.py RUN_ID --tree       detailed view (all steps and substeps) instead of the table

Every snapshot is also written, without colour, to hpc/logs/<RUN_ID>/audit.txt and audit.json.

Sources (read-only; one squeue and one sacct call per snapshot, no loops unless --watch):
  jobs.tsv          the jobs of the run (id, name, dependency, script, model/bench)
  squeue / sacct    Slurm state, queue reason, node, elapsed, time limit, exit code
  steps.tsv         ledger of finished steps (rc, seconds) written by run_pipeline.py
  steps/<key>/*.running   marker of the step that is executing now (live timer)
  steps/<key>/*.log       failure excerpts and per-model substeps
  <results>/raw/*.jsonl   per-condition progress of the matrix steps
  plan.json         steps each job will run (from `run_pipeline.py <stage> --list`, cached on first audit)

Levels and colours:  job = bold cyan   step = blue   substep = magenta   (status badge: green ok, red failed,
yellow running, grey waiting, cyan skipped, orange interrupted/partial).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

HPC = Path(__file__).resolve().parents[1]
N_TASKS = {"classeval": 100, "humanevalplus": 164}
STAGE_OF = {"00": "setup", "10": "matrix", "20": "rq2_builder", "21": "rq4_ref", "30": "rq2_llm",
            "31": "rq4_builder", "32": "rq4_repair", "33": "coding", "40": "analysis"}

# ── colour ───────────────────────────────────────────────────────────────────
USE_COLOR = False
CODES = {"job": "1;36", "step": "34", "sub": "35", "ok": "32", "fail": "31", "run": "1;33", "wait": "90",
         "skip": "36", "warn": "38;5;208", "dim": "90", "bold": "1", "head": "1;37"}


def c(kind: str, text: str) -> str:
    return f"\033[{CODES[kind]}m{text}\033[0m" if USE_COLOR else text


# status -> (symbol, colour kind, label)
BADGE = {"OK": ("✔", "ok", "OK"), "FAILED": ("✘", "fail", "FAILED"), "RUNNING": ("▶", "run", "RUNNING"),
         "PENDING": ("○", "wait", "PENDING"), "SKIPPED": ("↷", "skip", "DONE EARLIER"),
         "INTERRUPTED": ("!", "warn", "INTERRUPTED"), "PARTIAL": ("◐", "warn", "PARTIAL"),
         "CANCELLED": ("⊘", "fail", "CANCELLED"), "NOTRUN": ("–", "wait", "NOT RUN"),
         "TIMEOUT": ("⏱", "fail", "TIMEOUT")}


def badge(st: str) -> str:
    sym, kind, label = BADGE[st]
    return c(kind, f"{sym} {label:<12s}")


# ── helpers ──────────────────────────────────────────────────────────────────
def hms(sec: float | None) -> str:
    if sec is None:
        return "-"
    sec = int(max(sec, 0))
    d, r = divmod(sec, 86400)
    h, r = divmod(r, 3600)
    m, s = divmod(r, 60)
    return (f"{d}d{h:02d}h{m:02d}m" if d else f"{h}h{m:02d}m{s:02d}s" if h else f"{m}m{s:02d}s" if m else f"{s}s")


def slurm_secs(t: str) -> float | None:
    """'1-02:03:04' | '02:03:04' | '03:04' -> seconds (None for UNLIMITED / empty)."""
    if not t or t in ("UNLIMITED", "INVALID", "Unknown", "None"):
        return None
    d, _, rest = t.rpartition("-")
    parts = [float(x) for x in rest.split(":")]
    while len(parts) < 3:
        parts.insert(0, 0.0)
    return (int(d) if d else 0) * 86400 + parts[0] * 3600 + parts[1] * 60 + parts[2]


def ts(t: str) -> float | None:
    try:
        return datetime.strptime(t, "%Y-%m-%dT%H:%M:%S").timestamp()
    except ValueError:
        return None


def safe(s: str) -> str:
    return s.replace(":", "_").replace("/", "_")


def run(cmd: list[str], timeout: int = 60) -> str:
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False).stdout
    except (OSError, subprocess.SubprocessError):
        return ""


def tail_lines(p: Path, n: int = 3, width: int = 150) -> list[str]:
    try:
        lines = [x.rstrip() for x in p.read_text(errors="replace").splitlines() if x.strip()]
    except OSError:
        return []
    return [x[:width] for x in lines[-n:]]


KEY_LINE = re.compile(r"threshold|VERIFY FAILED|FAILED|ERROR|Error|Exception|MISSING|not responding|rc=[1-9]|Traceback")


def failure_excerpt(p: Path, n: int = 6, width: int = 150) -> list[str]:
    """The informative lines of a failed step's log: key lines of the latest attempt, else its tail."""
    try:
        text = p.read_text(errors="replace")
    except OSError:
        return []
    last = text.rsplit("\n===== ", 1)[-1]                       # latest attempt only
    lines = [x.rstrip() for x in last.splitlines() if x.strip()]
    keyed = [x for x in lines if KEY_LINE.search(x)]
    pick = keyed[-n:] if keyed else lines[-3:]
    return [x[:width] for x in pick]


def cfg(run_id: str) -> dict:
    f = HPC / "logs" / run_id / "run_config.env"
    f = f if f.exists() else HPC / "config" / "pipeline.env"
    out = run(["bash", "-c", f'set -a; source "{f}" >/dev/null 2>&1; env -0'])
    return dict(kv.split("=", 1) for kv in out.split("\0") if "=" in kv)


# ── data collection ──────────────────────────────────────────────────────────
def read_jobs(log_root: Path) -> list[dict]:
    jobs = []
    f = log_root / "jobs.tsv"
    for line in (f.read_text().splitlines() if f.exists() else []):
        p = line.split("\t")
        if len(p) < 4:
            continue
        extra = dict(kv.split("=", 1) for kv in (p[4].split() if len(p) > 4 else []) if "=" in kv)
        stage = STAGE_OF.get(p[3][:2], "?")
        key = "_".join(x for x in (stage, safe(extra.get("MODEL", "")), extra.get("BENCH", "")) if x)
        jobs.append({"id": p[0], "name": p[1], "dep": p[2], "script": p[3], "stage": stage, "key": key,
                     "model": extra.get("MODEL", ""), "bench": extra.get("BENCH", "")})
    return jobs


def slurm_state(ids: list[str]) -> dict[str, dict]:
    """sacct (history) overlaid with squeue (live: pending reason, running time)."""
    st: dict[str, dict] = {}
    j = ",".join(ids)
    for line in run(["sacct", "-X", "-n", "-P", "-j", j, "-o",
                     "JobID,State,Elapsed,Timelimit,ExitCode,NodeList,Submit,Start,End,Reason"]).splitlines():
        f = line.split("|")
        if len(f) >= 10:
            st[f[0]] = {"state": f[1].split()[0], "elapsed": slurm_secs(f[2]), "limit": slurm_secs(f[3]),
                        "exit": f[4], "node": f[5], "submit": ts(f[6]), "start": ts(f[7]), "end": ts(f[8]),
                        "reason": f[9]}
    for line in run(["squeue", "-h", "-j", j, "-o", "%i|%T|%r|%M|%l|%N"]).splitlines():
        f = line.split("|")
        if len(f) >= 6 and f[0] in st:
            st[f[0]].update(state=f[1], reason=f[2], elapsed=slurm_secs(f[3]) or st[f[0]]["elapsed"],
                            limit=slurm_secs(f[4]) or st[f[0]]["limit"], node=f[5] or st[f[0]]["node"])
    return st


def read_ledger(log_root: Path) -> dict[tuple[str, str], list[dict]]:
    out: dict[tuple[str, str], list[dict]] = {}
    f = log_root / "steps.tsv"
    for line in (f.read_text().splitlines()[1:] if f.exists() else []):
        p = line.split("\t")
        if len(p) >= 9:
            out.setdefault((p[3], p[4]), []).append({"job": p[1], "restart": p[2], "start": p[5], "end": p[6],
                                                     "sec": float(p[7]), "rc": int(p[8])})
    return out


def read_plan(log_root: Path, run_id: str, jobs: list[dict], conf: dict) -> dict[str, list[dict]]:
    """Planned steps per job key, from `run_pipeline.py <stage> --list` (cached in plan.json)."""
    cache = log_root / "plan.json"
    plan: dict[str, list[dict]] = json.loads(cache.read_text()) if cache.exists() else {}
    work = conf.get("WORK") or str(Path(os.environ.get("SCRATCH", "")) / "autom2m_hpc")
    env = {**os.environ, **conf, "HPC_DIR": str(HPC), "LOG_ROOT": str(log_root), "RUN_ID": run_id,
           "RUN_DIR": str(Path(work) / "runs" / run_id)}
    changed = False
    for j in jobs:
        if j["key"] in plan:
            continue
        cmd = [sys.executable, str(HPC / "scripts" / "run_pipeline.py"), j["stage"], "--list"]
        if j["model"]:
            cmd += ["--model", j["model"]]
        if j["bench"]:
            cmd += ["--bench", j["bench"]]
        try:
            out = subprocess.run(cmd, capture_output=True, text=True, timeout=60, env=env, check=False).stdout
        except (OSError, subprocess.SubprocessError):
            continue
        steps = []
        for line in out.splitlines():
            m = re.match(r"(\S+)\s+(.*?)(?:\s+models=(\S+))?$", line)
            if m:
                steps.append({"name": m.group(1), "models": (m.group(3) or "").split(",") if m.group(3) else []})
        if steps:
            plan[j["key"]] = steps
            changed = True
    if changed:
        try:
            cache.write_text(json.dumps(plan, indent=1))
        except OSError:
            pass
    return plan


# ── substeps ─────────────────────────────────────────────────────────────────
def matrix_substeps(step: dict, job: dict, conf: dict, results: Path, running: bool) -> list[dict]:
    """run_matrix_<bench>_passN: progress per condition from raw/<bench>__<model>.jsonl."""
    if not step["name"].startswith("run_matrix_") or not job["model"]:
        return []
    conds = [x for x in conf.get("CONDITIONS", "").split(",") if x]
    seeds = len([x for x in conf.get("SEEDS", "1,2,3").split(",") if x])
    exp = N_TASKS.get(job["bench"], 0) * seeds
    p = results / "raw" / f"{job['bench']}__{safe(job['model'])}.jsonl"
    last: dict = {}
    if p.exists():
        for line in p.read_text(errors="replace").splitlines():
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            last[(r.get("task"), r.get("condition"), r.get("seed"))] = r
    done = {k: 0 for k in conds}
    err = {k: 0 for k in conds}
    for r in last.values():
        k = r.get("condition")
        if k in done:
            if r.get("status") == "error":
                err[k] += 1
            else:
                done[k] += 1
    subs = []
    for k in conds:
        st = "OK" if exp and done[k] >= exp else "RUNNING" if running and done[k] else "PARTIAL" if done[k] else "PENDING"
        note = f"{done[k]}/{exp}" + (f"  {err[k]} error" if err[k] else "")
        subs.append({"name": k, "status": st, "note": note})
    return subs


def model_substeps(step: dict, logf: Path, step_status: str) -> list[dict]:
    """pull_models / verify_models: one line per model, taken from the step log."""
    if step["name"] not in ("pull_models", "verify_models") or not step["models"]:
        return []
    text = logf.read_text(errors="replace") if logf.exists() else ""
    subs, seen_running = [], False
    for tag in step["models"]:
        line = next((x for x in reversed(text.splitlines()) if re.search(rf"\] {re.escape(tag)}:", x)), "")
        note = line.split(f"{tag}:", 1)[1].strip()[:90] if line else ""
        if not line:
            pulled = re.search(rf"pulling {re.escape(tag)} \.\.\.", text)     # logged as "pulling <tag> ..."
            if pulled and step_status == "OK":
                st, note = "OK", "pulled"
            elif pulled and step_status == "RUNNING" and not seen_running:
                st, note, seen_running = "RUNNING", "downloading", True
            elif step_status == "OK":
                st = "OK"
            else:
                st = "RUNNING" if step_status == "RUNNING" and not seen_running else "PENDING"
                seen_running = seen_running or st == "RUNNING"
        elif re.search(r"MISSING|ok=False|fail|error", note, re.I):
            st = "FAILED"
        elif re.search(r"pulling|downloading|loading|verifying|%", note, re.I) and step_status == "RUNNING":
            st = "RUNNING"
            seen_running = True
        else:
            st = "OK"
        subs.append({"name": tag, "status": st, "note": note})
    return subs


# ── per-job assembly ─────────────────────────────────────────────────────────
def assemble(run_id: str) -> dict:
    log_root = HPC / "logs" / run_id
    conf = cfg(run_id)
    work = conf.get("WORK") or str(Path(os.environ.get("SCRATCH", "")) / "autom2m_hpc")
    results = Path(os.environ.get("AM2M_RESULTS") or Path(work) / "runs" / run_id / "results")
    jobs = read_jobs(log_root)
    sl = slurm_state([j["id"] for j in jobs]) if jobs else {}
    ledger = read_ledger(log_root)
    plan = read_plan(log_root, run_id, jobs, conf)
    now = time.time()
    names = {j["id"]: j for j in jobs}

    for j in jobs:
        s = sl.get(j["id"], {"state": "UNKNOWN", "elapsed": None, "limit": None, "exit": "", "node": "",
                             "submit": None, "start": None, "end": None, "reason": ""})
        j["slurm"] = s
        state = s["state"]
        jstate = ("RUNNING" if state == "RUNNING" else "PENDING" if state in ("PENDING", "REQUEUED", "CONFIGURING")
                  else "OK" if state == "COMPLETED" else "TIMEOUT" if state == "TIMEOUT" else "CANCELLED"
                  if state.startswith("CANCELLED") else "FAILED" if state != "UNKNOWN" else "PENDING")
        j["status"] = jstate
        logdir = log_root / "steps" / j["key"]
        steps = []
        for sp in plan.get(j["key"], []):
            name = sp["name"]
            rows = ledger.get((j["key"], name), [])
            logf = logdir / f"{name}.log"
            mk = logdir / f"{name}.running"
            info = json.loads(mk.read_text()) if mk.exists() else None
            marker = Path(work) / "runs" / run_id / "state" / j["key"] / f"{name}.done"
            sec, why = None, []
            if info and info.get("job") == j["id"] and jstate == "RUNNING":
                st, sec = "RUNNING", now - float(info["start"])
            elif info and info.get("job") == j["id"]:
                st, sec = "INTERRUPTED", None
                why = ["job ended while this step was running"]
            elif rows and rows[-1]["rc"] == 0:
                st, sec = "OK", sum(r["sec"] for r in rows if r["rc"] == 0)
            elif rows:
                st, sec = "FAILED", rows[-1]["sec"]
                why = failure_excerpt(logf)
            elif marker.exists():
                st = "SKIPPED"
            elif jstate in ("FAILED", "CANCELLED", "TIMEOUT"):
                st = "NOTRUN"
            else:
                st = "PENDING"
            step = {"name": name, "status": st, "sec": sec, "attempts": len(rows), "why": why}
            step["subs"] = (matrix_substeps(sp, j, conf, results, st == "RUNNING")
                            or model_substeps(sp, logf, st))
            steps.append(step)
        j["steps"] = steps
        # a job that says COMPLETED/RUNNING but has a failed step is flagged by its steps; surface job-level cause
        j["why"] = []
        if jstate in ("FAILED", "TIMEOUT"):
            errf = next(iter(sorted((log_root / "slurm").glob(f"*_{j['id']}.err"))), None)
            j["why"] = [x for x in (tail_lines(errf, 6) if errf else []) if "modules were not unloaded" not in x
                        and not x.startswith(("  ", "(Use"))][-3:]
        if any(x["status"] in ("FAILED", "INTERRUPTED") for x in steps):
            j["why"] = []                                        # the step below carries the cause
        if jstate == "CANCELLED" and s["start"] is None:
            j["why"] = ["never started: a job it depends on failed or was cancelled"]
        if jstate == "PENDING":
            deps = re.findall(r"\d{5,}", j["dep"])
            blocking = [d for d in deps if sl.get(d, {}).get("state") not in ("COMPLETED",)]
            kinds = {names[d]["name"].split("_")[1] + "_" + names[d]["name"].split("_")[2] for d in blocking if d in names}
            j["waiting"] = (f"{len(blocking)} job(s) first ({', '.join(sorted(kinds))[:70]})" if blocking else "")
    return {"run_id": run_id, "now": now, "jobs": jobs, "conf": conf, "log_root": str(log_root),
            "results": str(results)}


# ── rendering ────────────────────────────────────────────────────────────────
def table(a: dict, only: str, w: int) -> list[str]:
    """One row per job: state, node, times, step progress, current/failed step and a short detail."""
    cols = ["JOB", "STATE", "NODE", "RUN TIME", "QUEUED", "STEPS", "PROGRESS", "CURRENT / FAILED STEP", "DETAIL"]
    rows = []
    for j in a["jobs"]:
        st, s = j["steps"], j["slurm"]
        if only == "failed" and j["status"] not in ("FAILED", "TIMEOUT", "CANCELLED") \
                and not any(x["status"] in ("FAILED", "INTERRUPTED") for x in st):
            continue
        n_ok = sum(1 for x in st if x["status"] in ("OK", "SKIPPED"))
        cur = next((x for x in st if x["status"] in ("RUNNING", "FAILED", "INTERRUPTED")), None)
        detail = ""
        if cur:
            subs = cur["subs"]
            live = next((x for x in subs if x["status"] == "RUNNING"), None)
            if cur["status"] == "RUNNING":
                detail = f"{hms(cur['sec'])}" + (f"  {live['name']} {live['note']}".rstrip() if live else
                          (f"  {sum(1 for x in subs if x['status'] == 'OK')}/{len(subs)}" if subs else ""))
            elif cur["why"]:
                detail = next((x.strip(" │") for x in cur["why"] if KEY_LINE.search(x)), cur["why"][-1]).strip()
        elif j["status"] == "PENDING":
            detail = (f"waits: {s['reason']}" if s["reason"] not in ("", "None")
                      else "waits: Dependency" if j["dep"] else "queued")
        elif j["why"]:
            detail = "dependency failed, never started" if j["status"] == "CANCELLED" else j["why"][0]
        elif j["status"] == "OK":
            detail = "finished"
        run = hms(s["elapsed"]) if (s["elapsed"] and j["status"] != "PENDING") else "-"
        if j["status"] == "RUNNING" and s["limit"]:
            run += f" / {hms(s['limit'])}"
        wait = (s["start"] - s["submit"]) if s["start"] and s["submit"] else None
        bar = ""
        if st:
            k = round(8 * n_ok / len(st))
            bar = "█" * k + "░" * (8 - k)
        node = s["node"] if s["node"] not in ("", "None assigned") else "-"
        detail = re.sub(r"^\[\d{4}-\d\d-\d\d \d\d:\d\d:\d\d\]\s*", "", detail).strip()
        rows.append({"st": j["status"], "cells": [j["name"].replace("am2m_", "", 1), BADGE[j["status"]][2], node, run,
                                                   hms(wait) if wait is not None else "-", f"{n_ok}/{len(st)}" if st else "-",
                                                   bar, cur["name"] if cur else "-", detail]})
    if not rows:
        return [" (nothing to show)"]
    widths = [max(len(cols[i]), *(len(r["cells"][i]) for r in rows)) for i in range(len(cols))]
    widths[0], widths[8] = min(widths[0], 46), min(widths[8], max(20, w - sum(widths[:8]) - 3 * 9 - 2))
    clip = lambda t, n: t if len(t) <= n else t[: n - 1] + "…"
    sep = c("dim", "─┼─".join("─" * x for x in widths))
    out = ["  " + c("head", " │ ".join(f"{h:<{widths[i]}}" for i, h in enumerate(cols))), "  " + sep]
    for r in rows:
        cells = []
        for i, t in enumerate(r["cells"]):
            t = clip(t, widths[i])
            pad = " " * (widths[i] - len(t))
            kind = ("job" if i == 0 else BADGE[r["st"]][1] if i in (1, 6) else "step" if i == 7 else "dim" if i == 8 else None)
            cells.append((c(kind, t) if kind else t) + pad)
        sym = c(BADGE[r["st"]][1], BADGE[r["st"]][0])
        out.append(f"{sym} " + " │ ".join(cells))
    return out


def render(a: dict, only: str, tree: bool = False) -> str:
    out: list[str] = []
    w = shutil.get_terminal_size((130, 24)).columns
    jobs = a["jobs"]
    manifest = {}
    mf = Path(a["log_root"]) / "run_manifest.json"
    if mf.exists():
        try:
            manifest = json.loads(mf.read_text())
        except json.JSONDecodeError:
            pass
    git = manifest.get("git", manifest)
    out.append(c("head", "═" * min(w, 118)))
    out.append(c("head", f" AutoM2M run audit — {a['run_id']}") + c("dim", f"   snapshot {time.strftime('%F %T')}"))
    out.append(c("dim", f" account {a['conf'].get('ACCOUNT', '?')}   commit {str(git.get('commit', '?'))[:8]} "
                         f"({git.get('branch', '?')}{', dirty' if git.get('dirty_paths') else ''})   results {a['results']}"))
    out.append(c("head", "═" * min(w, 118)))

    counts: dict[str, int] = {}
    for j in jobs:
        counts[j["status"]] = counts.get(j["status"], 0) + 1
    sc: dict[str, int] = {}
    for j in jobs:
        for s in j["steps"]:
            sc[s["status"]] = sc.get(s["status"], 0) + 1
    order = ["OK", "RUNNING", "PENDING", "FAILED", "TIMEOUT", "CANCELLED"]
    out.append(" Jobs   " + "  ".join(f"{badge(k).strip()} {counts[k]}" for k in order if k in counts)
               + c("dim", f"   (total {len(jobs)})"))
    if sc:
        order2 = ["OK", "SKIPPED", "RUNNING", "PENDING", "FAILED", "INTERRUPTED", "NOTRUN"]
        out.append(" Steps  " + "  ".join(f"{badge(k).strip()} {sc[k]}" for k in order2 if k in sc))
    out.append(" Legend " + c("job", "job (node)") + " > " + c("step", "step") + " > " + c("sub", "substep") + "   "
               + "  ".join(badge(k).strip() for k in ("OK", "RUNNING", "PENDING", "FAILED", "INTERRUPTED")))
    out.append("")

    if not tree:
        out += table(a, only, w)
        out.append("")
        out.append(c("dim", f" detail of every step and substep: bash hpc/status.sh {a['run_id']} --tree   "
                            f"(only failures: --only failed)   refresh: --watch 120"))
        return "\n".join(out)
    shown = 0
    for j in jobs:
        if only == "failed" and j["status"] not in ("FAILED", "TIMEOUT", "CANCELLED") \
                and not any(s["status"] in ("FAILED", "INTERRUPTED") for s in j["steps"]):
            continue
        shown += 1
        s = j["slurm"]
        wait = (s["start"] - s["submit"]) if s["start"] and s["submit"] else None
        bits = [f"#{j['id']}"]
        if s["node"] and s["node"] not in ("None assigned", ""):
            bits.append(f"node {s['node']}")
        if s["state"] == "RUNNING" or s["elapsed"]:
            bits.append(("running " if j["status"] == "RUNNING" else "ran ") + hms(s["elapsed"])
                        + (f" / limit {hms(s['limit'])}" if s["limit"] else ""))
        if wait is not None:
            bits.append(f"queued {hms(wait)}")
        if j["status"] == "PENDING" and s["reason"] and s["reason"] != "None":
            bits.append(f"reason {s['reason']}")
        if j["status"] in ("FAILED", "TIMEOUT", "CANCELLED") and s["exit"] not in ("", "0:0"):
            bits.append(f"exit {s['exit']}")
        out.append(f"{badge(j['status'])} {c('job', j['name'])}  " + c("dim", "  ".join(bits)))
        if j.get("waiting"):
            out.append("   " + c("dim", f"waiting for {j['waiting']}"))
        for line in j["why"]:
            out.append("   " + c("fail", "│ " + line))
        if j["steps"] and all(x["status"] == "NOTRUN" for x in j["steps"]):
            out.append("   " + c("dim", f"└─ {len(j['steps'])} steps not run"))
            out.append("")
            continue
        if j["steps"] and j["status"] == "PENDING" and all(x["status"] == "PENDING" for x in j["steps"]):
            names = " › ".join(re.sub(r"^(run_matrix_|matrix_check_)", r"\1", x["name"]) for x in j["steps"])
            out.append("   " + c("dim", f"└─ {len(j['steps'])} steps queued: ") + c("step", names[:w - 30] + ("…" if len(names) > w - 30 else "")))
            out.append("")
            continue
        for i, st in enumerate(j["steps"]):
            last = i == len(j["steps"]) - 1
            br, cont = ("└─" if last else "├─"), ("   " if last else "│  ")
            extra = []
            if st["sec"] is not None:
                extra.append(("elapsed " if st["status"] == "RUNNING" else "took ") + hms(st["sec"]))
            if st["attempts"] > 1:
                extra.append(f"{st['attempts']} attempts")
            label = "%2d %s" % (i + 1, st["name"])
            out.append(f"   {c('dim', br)} {badge(st['status'])} {c('step', label)}  " + c("dim", "  ".join(extra)))
            for line in st["why"]:
                out.append(f"   {c('dim', cont)}    {c('fail' if st['status'] == 'FAILED' else 'warn', '│ ' + line)}")
            for k, sub in enumerate(st["subs"] if st["status"] not in ("NOTRUN", "SKIPPED", "PENDING") else []):
                sb = "└─" if k == len(st["subs"]) - 1 else "├─"
                out.append(f"   {c('dim', cont)}    {c('dim', sb)} {badge(sub['status'])} "
                           f"{c('sub', '%-30s' % sub['name'])} {c('dim', sub['note'])}")
        out.append("")
    if not shown:
        out.append(" (nothing to show)" if jobs else " no jobs recorded for this run yet")
    if jobs:
        out.append(c("dim", f" next: bash hpc/status.sh {a['run_id']}   (one snapshot; or --watch 120). "
                            f"failed step logs: hpc/logs/{a['run_id']}/steps/<stage>/<step>.log"))
    return "\n".join(out)


def strip_ansi(s: str) -> str:
    return re.sub(r"\033\[[0-9;]*m", "", s)


def main() -> int:
    global USE_COLOR
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("run_id", nargs="?")
    ap.add_argument("--watch", type=int, nargs="?", const=60, default=0, metavar="SEC",
                    help="live view: redraw every SEC seconds (default 60, minimum 60); stops when all jobs have ended")
    ap.add_argument("--no-color", action="store_true")
    ap.add_argument("--color", action="store_true", help="force colour even when not a terminal (e.g. | less -R)")
    ap.add_argument("--only", choices=["all", "failed"], default="all")
    ap.add_argument("--tree", action="store_true", help="detailed view: every step and substep of every job")
    a = ap.parse_args()
    runs = sorted((p for p in (HPC / "logs").glob("am2m-*") if p.is_dir()), key=lambda p: p.stat().st_mtime)
    run_id = a.run_id or (runs[-1].name if runs else "")
    if not run_id or not (HPC / "logs" / run_id).is_dir():
        print(f"no such run: {run_id or '(none)'} in {HPC / 'logs'}", file=sys.stderr)
        return 1
    USE_COLOR = not a.no_color and "NO_COLOR" not in os.environ and (a.color or sys.stdout.isatty())
    if a.watch and a.watch < 60:
        print("--watch is limited to >= 60 s to stay gentle on the scheduler; using 60", file=sys.stderr)
        a.watch = 60
    while True:
        data = assemble(run_id)
        text = render(data, a.only, a.tree)
        if a.watch and USE_COLOR:
            print("\033[2J\033[H", end="")
        print(text)
        root = Path(data["log_root"])
        try:
            (root / "audit.txt").write_text(strip_ansi(text) + "\n")
            slim = [{k: v for k, v in j.items() if k != "dep"} for j in data["jobs"]]
            (root / "audit.json").write_text(json.dumps({"run_id": run_id, "snapshot": time.strftime("%F %T"),
                                                         "jobs": slim}, indent=1, default=str))
        except OSError:
            pass
        if not a.watch:
            return 0
        if not any(j["status"] in ("PENDING", "RUNNING") for j in data["jobs"]):
            print(c("dim", " all jobs have ended — live view stopped"))
            return 0
        print(c("dim", f" live: next refresh in {a.watch}s   (Ctrl-C stops this view only; the jobs keep running)"))
        try:
            time.sleep(a.watch)
        except KeyboardInterrupt:
            print()
            return 0


if __name__ == "__main__":
    raise SystemExit(main())
