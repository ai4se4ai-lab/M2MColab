"""hpc/scripts/run_pipeline.py — run one stage of the AutoM2M study inside a Slurm job.

A stage is an ordered list of steps, each a `python -m evaluation.…` command of the
repository's harness (evaluation/) or a built-in action (pull/verify models,
completeness checks). For every step the runner

  * checks that the job's Ollama server is healthy (LLM steps),
  * loads the step's models once and warns if a model is not fully on the GPU,
  * writes the step's stdout+stderr to hpc/logs/<RUN_ID>/steps/<stage>/<step>.log,
  * appends a row to hpc/logs/<RUN_ID>/steps.tsv (job, restart, stage, step, start, end, rc, cmd),
  * leaves a .done marker in $RUN_DIR/state/ so a requeued job skips finished steps.

A failed step is logged and the stage continues with the next step; the exit code is
non-zero if any step failed. Environment (paths, AM2M_*, OLLAMA) is prepared by
hpc/slurm/common.sh.

Usage (inside a job):
    python hpc/scripts/run_pipeline.py setup
    python hpc/scripts/run_pipeline.py matrix --model qwen2.5-coder:7b --bench classeval
    python hpc/scripts/run_pipeline.py rq2_builder | rq4_ref --model M | rq2_llm |
                                       rq4_builder | rq4_repair --model M | coding | analysis
    python hpc/scripts/run_pipeline.py <stage> [--model M] --list      # print the steps only
"""
from __future__ import annotations

import argparse
import json
import os
import shlex
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

import requests

HPC = Path(os.environ.get("HPC_DIR", Path(__file__).resolve().parents[1]))
RUN_DIR = Path(os.environ.get("RUN_DIR", HPC / "results" / "local"))
LOG_ROOT = Path(os.environ.get("LOG_ROOT", HPC / "logs" / "local"))
RESULTS = Path(os.environ.get("AM2M_RESULTS", RUN_DIR / "results"))
OLLAMA = os.environ.get("AM2M_OLLAMA", "http://127.0.0.1:11434")
PY = sys.executable
JOB = os.environ.get("SLURM_JOB_ID", "0")
RESTART = os.environ.get("SLURM_RESTART_COUNT", "0")


def env(name: str, default: str = "") -> str:
    return os.environ.get(name, default)


def safe(s: str) -> str:
    return s.replace(":", "_").replace("/", "_")


def model_row(tag: str) -> tuple[int, int]:
    """(num_parallel, workers) from config/models.tsv."""
    f = LOG_ROOT / "models.tsv"
    for line in (f if f.exists() else HPC / "config" / "models.tsv").read_text().splitlines():
        f = line.split()
        if f and not f[0].startswith("#") and f[0] == tag:
            return int(f[1]), int(f[2])
    return 8, 8


def aux_models(var: str) -> list[str]:
    return [s.split("@")[0] for s in env(var).split(",") if s.strip()]


@dataclass
class Step:
    name: str
    cmd: list[str] | None = None            # a command, or None for a built-in action
    action: str = ""                        # built-in: pull_models | verify_models | matrix_check | preflight_check
    models: list[str] = field(default_factory=list)   # models to load before the step
    num_ctx: int = 16384                    # context the step's requests use (Ollama reloads on change)
    llm: bool = True
    env: dict = field(default_factory=dict)
    always: bool = False                    # rerun even if a .done marker exists


def m(*args: str) -> list[str]:
    return [PY, "-m", *args]


# ─────────────────────────────────────────────────────────────────────────────
# Stage definitions (paper Sec. 5; the order follows evaluation/scripts/run_qwen7b.sh)
# ─────────────────────────────────────────────────────────────────────────────

def stage_steps(stage: str, a: argparse.Namespace) -> list[Step]:
    M = a.model
    if stage == "setup":
        all_models = sorted(set(env("MODELS").split()) | set(aux_models("AM2M_CODERS")) | set(aux_models("AM2M_CRITICS"))
                            | set(aux_models("AM2M_SEEDER")) | set(aux_models("AM2M_JUDGE")))
        pf = env("PREFLIGHT_MODEL", "qwen2.5-coder:7b")
        pre_env = {"AM2M_RESULTS": str(RUN_DIR / "preflight")}
        return [
            Step("prefetch_data", [PY, str(HPC / "scripts" / "prefetch.py"), "download"], llm=False,
                 env={"HF_DATASETS_OFFLINE": "0", "HF_HUB_OFFLINE": "0"}),
            Step("verify_data", [PY, str(HPC / "scripts" / "prefetch.py"), "verify",
                                 "--min-classeval", env("CANONICAL_MIN_CLASSEVAL", "0.95"),
                                 "--min-humaneval", env("CANONICAL_MIN_HUMANEVAL", "0.98"),
                                 "--workers", env("SLURM_CPUS_PER_TASK", "8"),
                                 "--out", str(RUN_DIR / "setup" / "verify_data.json")], llm=False, always=True),
            Step("pull_models", action="pull_models", models=all_models, always=True),
            Step("verify_models", action="verify_models", models=all_models, always=True),
            Step("selftest_checker", m("evaluation.rq2.mutate", "--out", str(RUN_DIR / "preflight" / "rq2" / "mutants.jsonl")),
                 llm=False, env=pre_env, always=True),
            Step("preflight_classeval", m("evaluation.run_matrix", "--bench", "classeval", "--model", pf,
                                           "--conditions", "single,autom2m", "--seeds", "1", "--tasks", "first:1",
                                           "--workers", "2"), models=[pf], env=pre_env, always=True),
            Step("preflight_humanevalplus", m("evaluation.run_matrix", "--bench", "humanevalplus", "--model", pf,
                                               "--conditions", "single,autom2m", "--seeds", "1", "--tasks", "first:1",
                                               "--workers", "2"), models=[pf], env=pre_env, always=True),
            Step("preflight_check", action="preflight_check", llm=False, env=pre_env, always=True),
        ]
    if stage == "matrix":
        par, workers = model_row(M)
        steps = []
        for i in range(1, int(env("MATRIX_PASSES", "3")) + 1):
            steps.append(Step(f"run_matrix_{a.bench}_pass{i}",
                              m("evaluation.run_matrix", "--bench", a.bench, "--model", M, "--conditions", env("CONDITIONS"),
                                "--seeds", env("SEEDS", "1,2,3"), "--tasks", env("TASKS", ""), "--workers", str(workers)),
                              models=[M]))
            steps.append(Step(f"matrix_check_{a.bench}_pass{i}", action="matrix_check", llm=False, always=True))
        return steps
    if stage == "rq2_builder":
        B = env("RQ2_BUILDER_MODEL", "qwen2.5-coder:7b")
        _, w = model_row(B)
        return [
            Step("rq2_mutate", m("evaluation.rq2.mutate"), llm=False),
            Step("rq2_scale", m("evaluation.rq2.scale"), llm=False),
            Step("rq1_audit_whowhen", m("evaluation.rq1.audit_whowhen"), llm=False),
            Step("rq2_builder_ablation", m("evaluation.rq2.builder_ablation", "--model", B, "--tasks",
                                           env("RQ2_ABLATION_TASKS", "first:40"), "--builders", "v1,v2",
                                           "--workers", str(min(w, 12))), models=[B]),
            Step("rq2_autom2m_1ex", m("evaluation.run_matrix", "--bench", "classeval", "--model", B,
                                      "--conditions", "autom2m_1ex", "--seeds", "1", "--tasks", env("TASKS", ""),
                                      "--workers", str(w)), models=[B]),
        ]
    if stage == "rq4_ref":
        judge = aux_models("AM2M_JUDGE")[0]
        return [
            Step("rq4_inject_ref", m("evaluation.rq4.inject", "--team", "ref", "--model", M, "--per-class",
                                     env("RQ4_PER_CLASS", "40"), "--workers", "12"), models=[M]),
            Step("rq4_transcript_ref", m("evaluation.rq4.transcript", "--source", "ref", "--model", M, "--workers",
                                         env("AUX_WORKERS", "8")), models=sorted({M, judge}), num_ctx=32768),
        ]
    if stage == "rq4_builder":
        judge = aux_models("AM2M_JUDGE")[0]
        return [
            Step("rq4_inject_builder", m("evaluation.rq4.inject", "--team", "builder", "--model", M, "--per-class",
                                         env("RQ4_PER_CLASS", "40"), "--workers", "12"), models=[M]),
            Step("rq4_transcript_builder", m("evaluation.rq4.transcript", "--source", "builder", "--model", M,
                                             "--workers", env("AUX_WORKERS", "8")), models=sorted({M, judge}), num_ctx=32768),
        ]
    if stage == "rq4_repair":
        _, w = model_row(M)
        return [Step("rq4_repair", m("evaluation.rq4.repair", "--model", M, "--n", env("RQ4_REPAIR_N", "200"),
                                     "--workers", str(min(w, 12))), models=[M])]
    if stage == "rq2_llm":
        w = env("AUX_WORKERS", "8")
        return [
            Step("rq2_natural_mutants", m("evaluation.rq2.natural_mutants"), llm=False),
            Step("rq2_independent_seed", m("evaluation.rq2.independent", "seed", "--per-class", env("RQ2_PER_CLASS", "60"),
                                           "--workers", w), models=aux_models("AM2M_SEEDER"), num_ctx=32768),
            Step("rq2_independent_clean", m("evaluation.rq2.independent", "clean"), llm=False),
            Step("rq2_independent_critics", m("evaluation.rq2.independent", "critics", "--workers", w),
                 models=aux_models("AM2M_CRITICS"), num_ctx=32768),
        ]
    if stage == "coding":
        w = env("AUX_WORKERS", "8")
        coders = aux_models("AM2M_CODERS")
        return [
            Step("rq4_natural", m("evaluation.rq4.natural", "--n", env("RQ4_NATURAL_N", "150"), "--workers", w),
                 models=coders, num_ctx=32768),
            Step("rq1_code_whowhen", m("evaluation.rq1.code_failures", "--source", "whowhen", "--workers", w),
                 models=coders, num_ctx=32768),
            Step("rq1_code_ours", m("evaluation.rq1.code_failures", "--source", "ours", "--n", env("RQ1_OURS_N", "400"),
                                    "--workers", w), models=coders, num_ctx=32768),
            Step("rq3_code_runs", m("evaluation.rq3.code_runs", "--workers", w), models=coders, num_ctx=32768),
        ]
    if stage == "analysis":
        out = LOG_ROOT / "results"          # collected next to the run's logs
        return [
            Step("rq2_natural_mutants_refresh", m("evaluation.rq2.natural_mutants"), llm=False, always=True),
            Step("analyze", m("evaluation.analysis.analyze"), llm=False, always=True),
            Step("report", m("evaluation.analysis.report"), llm=False, always=True),
            Step("web_export", m("evaluation.analysis.web_export"), llm=False, always=True),
            Step("summarize", [PY, str(HPC / "scripts" / "summarize.py"), "--results", str(RESULTS),
                               "--logs", str(LOG_ROOT)], llm=False, always=True),
            Step("collect", ["bash", str(HPC / "slurm" / "collect_results.sh"), str(RESULTS), str(out)], llm=False, always=True),
        ]
    raise SystemExit(f"unknown stage {stage}")


# ─────────────────────────────────────────────────────────────────────────────
# Ollama helpers
# ─────────────────────────────────────────────────────────────────────────────

def ollama_ok() -> bool:
    try:
        return requests.get(f"{OLLAMA}/api/version", timeout=10).ok
    except requests.RequestException:
        return False


def local_models() -> dict[str, str]:
    r = requests.get(f"{OLLAMA}/api/tags", timeout=30)
    r.raise_for_status()
    return {x["name"]: x.get("digest", "") for x in r.json().get("models", [])}


def has_model(tag: str, have: dict) -> bool:
    return tag in have or (":" not in tag and f"{tag}:latest" in have)


def pull(tag: str, log, attempts: int = 3) -> bool:
    """Pull a model; a dropped registry connection (error EOF) is retried, and Ollama resumes partial blobs."""
    for attempt in range(1, attempts + 1):
        log(f"pulling {tag} ..." + (f" (attempt {attempt}/{attempts})" if attempt > 1 else ""))
        last = ""
        try:
            with requests.post(f"{OLLAMA}/api/pull", json={"model": tag, "stream": True}, stream=True,
                               timeout=None) as r:
                if not r.ok:
                    log(f"  ERROR http {r.status_code} {r.text[:200]}")
                    continue
                failed = False
                for line in r.iter_lines():
                    if not line:
                        continue
                    d = json.loads(line)
                    if "error" in d:
                        log(f"  ERROR {d['error']}")
                        failed = True
                        break
                    st = d.get("status", "")
                    if st != last:  # one line per phase, not per progress tick
                        log(f"  {st}")
                        last = st
                if not failed:
                    return True
        except requests.RequestException as e:
            log(f"  ERROR {type(e).__name__}: {e}")
    return False


def load_models(models: list[str], num_ctx: int, log) -> None:
    """Load each model with the step's context size and report its GPU residency."""
    for tag in models:
        t0 = time.time()
        r = requests.post(f"{OLLAMA}/api/generate", timeout=1800,
                          json={"model": tag, "prompt": "", "keep_alive": "24h", "options": {"num_ctx": num_ctx}})
        log(f"loaded {tag} (num_ctx={num_ctx}) in {time.time() - t0:.0f}s, http {r.status_code}")
    try:
        for x in requests.get(f"{OLLAMA}/api/ps", timeout=30).json().get("models", []):
            size, vram = x.get("size", 0), x.get("size_vram", 0)
            share = vram / size if size else 0
            flag = "" if share >= 0.999 else "   <-- WARNING: not fully on GPU (lower OLLAMA_NUM_PARALLEL in models.tsv)"
            log(f"resident {x['name']}: {size / 2**30:.1f} GiB, {100 * share:.0f}% GPU{flag}")
    except requests.RequestException as e:
        log(f"api/ps failed: {e}")


# ─────────────────────────────────────────────────────────────────────────────
# Built-in actions
# ─────────────────────────────────────────────────────────────────────────────

def act_pull_models(step: Step, log) -> int:
    have = local_models()
    rc = 0
    for tag in step.models:
        if has_model(tag, have):
            log(f"{tag}: cached ({have.get(tag, '')[:12]})")
        elif not pull(tag, log):
            rc = 1
    return rc


def act_verify_models(step: Step, log) -> int:
    have = local_models()
    out, rc = {}, 0
    for tag in step.models:
        if not has_model(tag, have):
            log(f"{tag}: MISSING")
            rc = 1
            continue
        t0 = time.time()
        r = requests.post(f"{OLLAMA}/api/generate", timeout=1800,
                          json={"model": tag, "prompt": "Reply with OK.", "stream": False, "keep_alive": 0,
                                "options": {"num_predict": 4, "num_ctx": 4096, "temperature": 0}})
        ok = r.ok and "response" in r.json()
        ps = {x["name"]: x for x in requests.get(f"{OLLAMA}/api/ps", timeout=30).json().get("models", [])}
        info = requests.post(f"{OLLAMA}/api/show", json={"model": tag}, timeout=60).json()
        det = info.get("details", {})
        out[tag] = {"digest": have.get(tag), "ok": ok, "seconds": round(time.time() - t0, 1),
                    "quantization": det.get("quantization_level"), "parameter_size": det.get("parameter_size"),
                    "family": det.get("family"),
                    "gpu_share": (ps[tag]["size_vram"] / ps[tag]["size"]) if tag in ps and ps[tag].get("size") else None}
        log(f"{tag}: ok={ok} {det.get('parameter_size')} {det.get('quantization_level')} "
            f"digest={str(have.get(tag))[:12]} gpu={out[tag]['gpu_share']}")
        rc |= 0 if ok else 1
        requests.post(f"{OLLAMA}/api/generate", json={"model": tag, "keep_alive": 0}, timeout=120)  # unload
    dest = RUN_DIR / "setup"
    dest.mkdir(parents=True, exist_ok=True)
    (dest / "model_digests.json").write_text(json.dumps(out, indent=1))
    return rc


def _raw_rows(path: Path) -> list[dict]:
    rows = []
    if path.exists():
        for line in path.read_text().splitlines():
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    return rows


def matrix_missing(bench: str, model: str) -> tuple[int, int, int]:
    """(expected, done, errors) for one model and benchmark over the configured matrix."""
    from evaluation.run_matrix import raw_path, select_tasks  # needs the venv

    ids = select_tasks(bench, env("TASKS", ""))
    conds = env("CONDITIONS").split(",")
    seeds = [int(s) for s in env("SEEDS", "1,2,3").split(",")]
    expected = {(t, c, s) for t in ids for c in conds for s in seeds}
    rows = _raw_rows(raw_path(bench, model))
    done = {(r["task"], r["condition"], r["seed"]) for r in rows if r.get("status") != "error"}
    errs = {(r["task"], r["condition"], r["seed"]) for r in rows if r.get("status") == "error"} - done
    return len(expected), len(expected & done), len(errs & expected)


def act_matrix_check(step: Step, log, a) -> int:
    exp, done, errs = matrix_missing(a.bench, a.model)
    log(f"{a.model} {a.bench}: {done}/{exp} runs complete, {errs} still in error")
    return 0 if done == exp else 2


def act_preflight_check(step: Step, log) -> int:
    res = Path(step.env["AM2M_RESULTS"])
    model = env("PREFLIGHT_MODEL", "qwen2.5-coder:7b")
    rc = 0
    for bench in ("classeval", "humanevalplus"):
        rows = _raw_rows(res / "raw" / f"{bench}__{safe(model)}.jsonl")
        conds = {r["condition"]: r for r in rows}
        for c in ("single", "autom2m"):
            r = conds.get(c)
            if r is None or r.get("status") == "error":
                log(f"FAIL {bench}/{c}: {'missing' if r is None else r.get('error') or r.get('status')}")
                rc = 1
            else:
                log(f"ok   {bench}/{c}: status={r['status']} success={r['success']} tests={r.get('tests_passed')}/"
                    f"{r.get('tests_run')} out_tokens={r.get('total', {}).get('out_tokens')} {r.get('seconds')}s")
    return rc


# ─────────────────────────────────────────────────────────────────────────────
# Runner
# ─────────────────────────────────────────────────────────────────────────────

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("stage")
    ap.add_argument("--model", default="")
    ap.add_argument("--bench", default="")
    ap.add_argument("--list", action="store_true", help="print the steps and exit")
    a = ap.parse_args()

    steps = stage_steps(a.stage, a)
    key = "_".join(x for x in (a.stage, safe(a.model), a.bench) if x)
    if a.list:
        for s in steps:
            print(f"{s.name:32s} {shlex.join(s.cmd) if s.cmd else '[' + s.action + ']'}"
                  + (f"   models={','.join(s.models)}" if s.models else ""))
        return 0

    state = RUN_DIR / "state" / key
    logdir = LOG_ROOT / "steps" / key
    state.mkdir(parents=True, exist_ok=True)
    logdir.mkdir(parents=True, exist_ok=True)
    ledger = LOG_ROOT / "steps.tsv"
    if not ledger.exists():
        ledger.write_text("time\tjob\trestart\tstage\tstep\tstart\tend\tseconds\trc\tcommand\n")

    failed = []
    print(f"=== stage {key}: {len(steps)} steps", flush=True)
    matrix_complete = False
    for s in steps:
        marker = state / f"{s.name}.done"
        if s.name.startswith("run_matrix_") and matrix_complete:
            continue
        if marker.exists() and not s.always:
            print(f"--- skip {s.name} (done earlier)", flush=True)
            continue
        logf = logdir / f"{s.name}.log"
        start = time.time()
        running = logdir / f"{s.name}.running"      # read by hpc/scripts/audit.py for the live step timer
        running.write_text(json.dumps({"job": JOB, "restart": RESTART, "start": start}))
        with logf.open("a") as fh:
            def log(msg: str, _fh=fh) -> None:
                line = f"[{time.strftime('%F %T')}] {msg}"
                print(line, file=_fh, flush=True)
                print(f"    {msg}", flush=True)

            print(f"--- {time.strftime('%F %T')} step {s.name}  (log: {logf})", flush=True)
            fh.write(f"\n===== {time.strftime('%F %T')} job {JOB} restart {RESTART} =====\n")
            if s.cmd:
                fh.write("$ " + shlex.join(s.cmd) + "\n")
            fh.flush()
            if s.llm and not ollama_ok():
                log(f"Ollama at {OLLAMA} is not responding — stopping the stage")
                rc = 3
            else:
                if s.models and not s.action:
                    load_models(s.models, s.num_ctx, log)
                step_env = {**os.environ, **s.env}
                try:
                    if s.action == "pull_models":
                        rc = act_pull_models(s, log)
                    elif s.action == "verify_models":
                        rc = act_verify_models(s, log)
                    elif s.action == "matrix_check":
                        rc = act_matrix_check(s, log, a)
                        matrix_complete = rc == 0
                        last = s.name.endswith("_pass" + env("MATRIX_PASSES", "3"))
                        rc = 0 if (matrix_complete or not last) else rc
                    elif s.action == "preflight_check":
                        rc = act_preflight_check(s, log)
                    else:
                        rc = subprocess.run(s.cmd, stdout=fh, stderr=subprocess.STDOUT, env=step_env,
                                            cwd=str(HPC.parent)).returncode
                except Exception as exc:  # noqa: BLE001
                    log(f"step raised {type(exc).__name__}: {exc}")
                    rc = 4
        end = time.time()
        running.unlink(missing_ok=True)
        with ledger.open("a") as lf:
            lf.write(f"{time.strftime('%F %T')}\t{JOB}\t{RESTART}\t{key}\t{s.name}\t"
                     f"{time.strftime('%F %T', time.localtime(start))}\t{time.strftime('%F %T', time.localtime(end))}\t"
                     f"{end - start:.0f}\t{rc}\t{shlex.join(s.cmd) if s.cmd else s.action}\n")
        print(f"    rc={rc}  {end - start:.0f}s", flush=True)
        if rc == 0:
            marker.write_text(time.strftime("%F %T") + "\n")
        else:
            failed.append(s.name)
            if rc == 3 or (a.stage == "setup" and s.name in ("prefetch_data", "verify_data", "pull_models")):
                break  # nothing later in the stage can succeed
    if a.stage == "matrix" and not matrix_complete:
        failed.append("matrix_incomplete")
    print(f"=== stage {key} finished; failed steps: {failed or 'none'}", flush=True)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
