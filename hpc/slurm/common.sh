#!/bin/bash
# Shared functions for the AutoM2M Slurm jobs (sourced by every hpc/slurm/NN_*.sh).
#
# Every job is started by submit_pipeline.sh with --export=ALL,HPC_DIR=...,RUN_ID=...
# (Slurm runs a copy of the script from its spool directory, so the script's own
# location cannot be used to find the repository.)
#
# A job: loads modules, builds a venv in $SLURM_TMPDIR, starts its own Ollama server
# on 127.0.0.1 (GPU jobs), samples the GPU, runs hpc/scripts/run_pipeline.py for its
# stage, and requeues itself (same job ID, so dependencies hold) when it is about to
# hit its time limit. All finished work is skipped on restart.

: "${HPC_DIR:?HPC_DIR not set (submit with hpc/slurm/submit_pipeline.sh)}"
: "${RUN_ID:?RUN_ID not set (submit with hpc/slurm/submit_pipeline.sh)}"
REPO_DIR="$(cd "$HPC_DIR/.." && pwd)"
LOG_ROOT="$HPC_DIR/logs/$RUN_ID"

# Frozen per-run configuration (written by submit_pipeline.sh); fall back to the live file.
# set -a exports every variable of the config (MODELS, CONDITIONS, SEEDS, RQ*_...) to the Python steps,
# which read them with os.environ in hpc/scripts/run_pipeline.py.
set -a
if [ -f "$LOG_ROOT/run_config.env" ]; then
    # shellcheck disable=SC1091
    source "$LOG_ROOT/run_config.env"
else
    # shellcheck disable=SC1091
    source "$HPC_DIR/config/pipeline.env"
fi
set +a

# SLURM_RESTART_COUNT is not reliably set after `scontrol requeue` on this cluster (tested: it stayed 0 while Slurm
# counted 5 restarts), which would disable MAX_REQUEUES. Ask Slurm for the real count once, at job start.
if [ -n "${SLURM_JOB_ID:-}" ]; then
    _restarts=$(scontrol show job "$SLURM_JOB_ID" 2>/dev/null | grep -o 'Restarts=[0-9]*' | head -1 | cut -d= -f2)
    [ -n "$_restarts" ] && export SLURM_RESTART_COUNT="$_restarts"
fi

RUN_DIR="$WORK/runs/$RUN_ID"
JOB_TAG="${SLURM_JOB_NAME:-local}_${SLURM_JOB_ID:-0}"
OLLAMA_PID=""
GPU_MON_PID=""
STAGE_PID=""

# ── Logging ──────────────────────────────────────────────────────────────────
log_info()  { echo "[$(date '+%F %T')] [INFO]  $*"; }
log_warn()  { echo "[$(date '+%F %T')] [WARN]  $*"; }
log_error() { echo "[$(date '+%F %T')] [ERROR] $*" >&2; }

# ── Exit / requeue handling ──────────────────────────────────────────────────
_cleanup() {
    # The stage runs in its own process group (run_stage uses setsid): signal the whole group, so that run_matrix and
    # its worker pool stop too. Signalling only run_pipeline.py left them running against the Ollama server stopped
    # below, and each requeue wrote a burst of "Could not reach Ollama" error rows (observed: ~1,300 in one job).
    if [ -n "$STAGE_PID" ]; then
        kill -TERM -- "-$STAGE_PID" 2>/dev/null || kill -TERM "$STAGE_PID" 2>/dev/null
        local _i
        for _i in $(seq 1 30); do kill -0 -- "-$STAGE_PID" 2>/dev/null || break; sleep 1; done
        kill -KILL -- "-$STAGE_PID" 2>/dev/null
    fi
    [ -n "$GPU_MON_PID" ] && kill "$GPU_MON_PID" 2>/dev/null
    if [ -n "$OLLAMA_PID" ] && kill -0 "$OLLAMA_PID" 2>/dev/null; then
        log_info "Stopping Ollama (PID $OLLAMA_PID)"
        kill "$OLLAMA_PID" 2>/dev/null
    fi
    STAGE_PID=""; GPU_MON_PID=""; OLLAMA_PID=""
}

_on_exit() {
    local code=$?
    _cleanup
    local status="ok"
    [ "$code" -ne 0 ] && status="FAILED($code)"
    printf '%s\t%s\t%s\t%s\t%s\t%s\n' "$(date '+%F %T')" "${SLURM_JOB_ID:-0}" "${SLURM_JOB_NAME:-local}" \
        "${SLURM_RESTART_COUNT:-0}" "$status" "$(hostname)" >> "$LOG_ROOT/jobs_finished.tsv"
    [ "$code" -ne 0 ] && log_error "Job $JOB_TAG finished with exit code $code"
    log_info "Job $JOB_TAG end: $(date)"
}

# Slurm sends USR1 to this shell 15 min before the time limit (--signal=B:USR1@900).
_on_timelimit() {
    local n="${SLURM_RESTART_COUNT:-0}"
    log_warn "Time limit approaching (restart $n of max $MAX_REQUEUES)"
    _cleanup
    if [ "$n" -lt "$MAX_REQUEUES" ]; then
        log_info "Requeueing job $SLURM_JOB_ID; finished runs and steps are skipped on restart"
        printf '%s\t%s\t%s\t%s\trequeued\t%s\n' "$(date '+%F %T')" "$SLURM_JOB_ID" "$SLURM_JOB_NAME" "$n" \
            "$(hostname)" >> "$LOG_ROOT/jobs_finished.tsv"
        scontrol requeue "$SLURM_JOB_ID"
        trap - EXIT
        exit 0
    fi
    log_error "Maximum number of requeues reached; leaving the work unfinished"
    exit 99
}

setup_traps() {
    trap _on_exit EXIT
    trap _on_timelimit USR1
}

# ── Job header ───────────────────────────────────────────────────────────────
print_job_header() {
    mkdir -p "$LOG_ROOT"/{slurm,steps,env,gpu,ollama} "$RUN_DIR"/{results,state}
    log_info "============================================================"
    log_info "Job:      ${SLURM_JOB_NAME:-local} (${SLURM_JOB_ID:-n/a}), restart ${SLURM_RESTART_COUNT:-0}"
    log_info "Node:     $(hostname)   CPUs: ${SLURM_CPUS_PER_TASK:-?}   Mem: ${SLURM_MEM_PER_NODE:-?}M"
    log_info "GPUs:     ${CUDA_VISIBLE_DEVICES:-none}"
    log_info "RUN_ID:   $RUN_ID"
    log_info "Repo:     $REPO_DIR @ $(git -C "$REPO_DIR" rev-parse --short HEAD 2>/dev/null || echo '?')"
    log_info "Results:  $RUN_DIR/results"
    log_info "Logs:     $LOG_ROOT"
    log_info "Start:    $(date)"
    log_info "============================================================"
    if command -v nvidia-smi >/dev/null 2>&1 && [ -n "${CUDA_VISIBLE_DEVICES:-}" ]; then
        nvidia-smi --query-gpu=index,name,memory.total,driver_version --format=csv,noheader | sed 's/^/[gpu] /'
    fi
}

# ── Python environment ───────────────────────────────────────────────────────
# Alliance practice: virtualenv in $SLURM_TMPDIR, packages from the wheelhouse.
setup_python() {
    module purge
    # shellcheck disable=SC2086
    module load $MODULES
    local venv="${SLURM_TMPDIR:-$RUN_DIR/tmp}/venv"
    if [ ! -x "$venv/bin/python" ]; then
        log_info "Creating virtualenv $venv"
        virtualenv --no-download -q "$venv"
        # shellcheck disable=SC1091
        source "$venv/bin/activate"
        pip install --no-index -q --upgrade pip
        if ! pip install --no-index -q --find-links "$WHEELS" -r "$HPC_DIR/config/requirements.txt"; then
            log_error "pip install failed (are the extra wheels in $WHEELS? run 00_setup first)"
            return 1
        fi
    else
        # shellcheck disable=SC1091
        source "$venv/bin/activate"
    fi
    pip freeze > "$LOG_ROOT/env/pip_freeze_${JOB_TAG}.txt" 2>/dev/null

    # the repository's evaluation/ and src/; prepend, because the Alliance python module's
    # sitecustomize (which exposes module-provided packages like pyarrow) lives on PYTHONPATH
    export PYTHONPATH="$REPO_DIR:$REPO_DIR/src${PYTHONPATH:+:$PYTHONPATH}"
    export AM2M_REPO="$REPO_DIR"
    export AM2M_RESULTS="${AM2M_RESULTS:-$RUN_DIR/results}"
    # evaluation/ defaults to the repository (docs/, web/); keep every output of a run in its results dir
    export AM2M_DOCS_OUT="$AM2M_RESULTS/docs_out" AM2M_WEB_OUT="$AM2M_RESULTS/web/results.json"
    export AM2M_REPORT="$AM2M_RESULTS/report.md"
    export AM2M_DATA AM2M_BUILDER AM2M_CODERS AM2M_CRITICS AM2M_SEEDER AM2M_JUDGE NLTK_DATA_DIR
    export AM2M_SANDBOX_PY="$venv/bin/python"            # LLM-written code and hidden tests run here
    export AGENTHOT_SANDBOX=process
    export HF_HOME HF_DATASETS_OFFLINE=1 HF_HUB_OFFLINE=1
    export TMPDIR="${SLURM_TMPDIR:-$RUN_DIR/tmp}"
    export MPLBACKEND=Agg PYTHONUNBUFFERED=1 OMP_NUM_THREADS=1
    export RUN_ID RUN_DIR LOG_ROOT HPC_DIR REPO_DIR
    mkdir -p "$TMPDIR"
    {
        echo "# $JOB_TAG $(date)"; module -t list 2>&1; echo
        env | grep -E '^(AM2M_|HF_|OLLAMA_|SLURM_|CUDA_|PYTHONPATH|TMPDIR|AGENTHOT_)' | sort
    } > "$LOG_ROOT/env/env_${JOB_TAG}.txt"
}

# ── Model sizing (models.tsv) ────────────────────────────────────────────────
model_field() {   # model_field <tag> <column 2..4>
    local f="$LOG_ROOT/models.tsv"; [ -f "$f" ] || f="$HPC_DIR/config/models.tsv"
    awk -v t="$1" -v c="$2" '$1==t {print $c; exit}' "$f"
}

# ── Ollama (one private server per job) ──────────────────────────────────────
start_ollama() {  # start_ollama <num_parallel> <max_loaded_models>
    local par="${1:-8}" maxl="${2:-1}"
    local bin="$OLLAMA_PREFIX/bin/ollama"
    if [ ! -x "$bin" ] || [ ! -d "$OLLAMA_PREFIX/lib/ollama" ]; then
        log_error "Ollama $OLLAMA_VERSION not installed at $OLLAMA_PREFIX (run the 00_setup stage)"
        return 1
    fi
    local port
    port=$(python3 -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1",0)); print(s.getsockname()[1]); s.close()')
    export OLLAMA_HOST="127.0.0.1:$port" OLLAMA_MODELS OLLAMA_NUM_PARALLEL="$par" OLLAMA_MAX_LOADED_MODELS="$maxl"
    export OLLAMA_KEEP_ALIVE=24h OLLAMA_FLASH_ATTENTION=1 OLLAMA_NOPRUNE=1 OLLAMA_MAX_QUEUE=2048
    export AM2M_OLLAMA="http://127.0.0.1:$port"
    local olog="$LOG_ROOT/ollama/ollama_${JOB_TAG}_r${SLURM_RESTART_COUNT:-0}.log"
    log_info "Starting Ollama $OLLAMA_VERSION on $OLLAMA_HOST (NUM_PARALLEL=$par, MAX_LOADED_MODELS=$maxl)"
    "$bin" serve > "$olog" 2>&1 &
    OLLAMA_PID=$!
    # readiness without sleep: curl retries the connection itself (up to ~3 min)
    if ! curl -sf --retry 90 --retry-delay 2 --retry-connrefused --retry-all-errors -m 5 \
            "$AM2M_OLLAMA/api/version" > "$LOG_ROOT/env/ollama_version_${JOB_TAG}.json"; then
        log_error "Ollama did not become ready; see $olog"
        return 1
    fi
    log_info "Ollama ready: $(cat "$LOG_ROOT/env/ollama_version_${JOB_TAG}.json")  log: $olog"
}

start_gpu_monitor() {
    command -v nvidia-smi >/dev/null 2>&1 || return 0
    nvidia-smi --query-gpu=timestamp,index,utilization.gpu,utilization.memory,memory.used,memory.total,power.draw,temperature.gpu \
        --format=csv -l 60 > "$LOG_ROOT/gpu/gpu_${JOB_TAG}_r${SLURM_RESTART_COUNT:-0}.csv" 2>/dev/null &
    GPU_MON_PID=$!
}

# ── Run one stage of hpc/scripts/run_pipeline.py in the background ───────────
# (background + wait lets the USR1 time-limit trap fire immediately)
run_stage() {
    setsid python "$HPC_DIR/scripts/run_pipeline.py" "$@" &
    STAGE_PID=$!
    wait "$STAGE_PID"
    local rc=$?
    STAGE_PID=""
    return $rc
}

# Standard body of a GPU stage job: run_gpu_stage <num_parallel> <max_loaded> <run_pipeline args...>
run_gpu_stage() {
    local par="$1" maxl="$2"; shift 2
    setup_traps
    print_job_header
    setup_python || return 1
    start_ollama "$par" "$maxl" || return 1
    start_gpu_monitor
    run_stage "$@"
}
