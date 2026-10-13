#!/bin/bash
# Submit the AutoM2M EMSE study (paper Sec. 5) as a graph of Slurm jobs on Nibi.
#
#   bash hpc/slurm/submit_pipeline.sh                      # full study, new RUN_ID
#   bash hpc/slurm/submit_pipeline.sh --dry-run            # print the sbatch commands only
#   bash hpc/slurm/submit_pipeline.sh --run-id R --skip-setup --only matrix,coding   # resubmit stages of run R
#   bash hpc/slurm/submit_pipeline.sh --run-id R --analysis-only                     # intermediate analysis
#
# Options:
#   --run-id ID        reuse/continue a run (default: am2m-<date>-<time>)
#   --models "a b"     subject models for the matrix (default: MODELS in config/pipeline.env)
#   --account ACC      Slurm account (default: ACCOUNT in config/pipeline.env)
#   --skip-setup       do not submit 00_setup (it already succeeded once)
#   --only LIST        comma list of stages: setup,matrix,rq2_builder,rq4_ref,rq2_llm,rq4_builder,rq4_repair,coding,analysis
#   --analysis-only    same as --skip-setup --only analysis
#   --dry-run          show what would be submitted
#   --watch [SEC]      after submitting, stay in a live status table (refresh every SEC s, default 60, minimum 60);
#                      Ctrl-C leaves the view only, the jobs keep running
#
# Graph:  00_setup ─afterok─► 10_matrix[model x bench], 20_rq2_builder, 21_rq4_ref[model]
#         all 10_matrix ─afterany─► 30_rq2_llm, 31_rq4_builder, 32_rq4_repair[model], 33_coding
#         everything ─afterany─► 40_analysis  →  hpc/logs/<RUN_ID>/results/
# The login node only writes the run configuration and calls sbatch; all work runs in jobs.
set -euo pipefail

HPC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck disable=SC1091
source "$HPC_DIR/config/pipeline.env"

RUN_ID=""; ONLY=""; SKIP_SETUP=false; DRY=false; MODELS_ARG=""; WATCH=0
while [[ $# -gt 0 ]]; do
    case "$1" in
        --run-id)        RUN_ID="$2"; shift 2 ;;
        --models)        MODELS_ARG="$2"; shift 2 ;;
        --account)       ACCOUNT="$2"; shift 2 ;;
        --skip-setup)    SKIP_SETUP=true; shift ;;
        --only)          ONLY="$2"; shift 2 ;;
        --analysis-only) SKIP_SETUP=true; ONLY="analysis"; shift ;;
        --dry-run)       DRY=true; shift ;;
        --watch)         if [[ "${2:-}" =~ ^[0-9]+$ ]]; then WATCH="$2"; shift 2; else WATCH=60; shift; fi ;;
        -h|--help)       sed -n '2,24p' "$0"; exit 0 ;;
        *) echo "unknown option: $1 (see --help)" >&2; exit 1 ;;
    esac
done
[ -n "$MODELS_ARG" ] && MODELS="$MODELS_ARG"
RUN_ID="${RUN_ID:-am2m-$(date +%Y%m%d-%H%M%S)}"
LOG_ROOT="$HPC_DIR/logs/$RUN_ID"
RUN_DIR="$WORK/runs/$RUN_ID"
want() { [ -z "$ONLY" ] || [[ ",$ONLY," == *",$1,"* ]]; }
$SKIP_SETUP && [ -z "$ONLY" ] && ONLY="matrix,rq2_builder,rq4_ref,rq2_llm,rq4_builder,rq4_repair,coding,analysis"

safe() { echo "${1//[:\/]/_}"; }
model_field() { awk -v t="$1" -v c="$2" '$1==t {print $c; exit}' "$HPC_DIR/config/models.tsv"; }
for m in $MODELS; do
    [ -n "$(model_field "$m" 2)" ] || { echo "model $m is not listed in config/models.tsv" >&2; exit 1; }
done

# ── Freeze the configuration of this run ─────────────────────────────────────
if ! $DRY; then
    mkdir -p "$LOG_ROOT"/{slurm,steps,env,gpu,ollama} "$RUN_DIR"/{results,state}
    if [ ! -f "$LOG_ROOT/run_config.env" ]; then
        { cat "$HPC_DIR/config/pipeline.env"; echo; echo "# frozen by submit_pipeline.sh $(date)"
          echo "MODELS=\"$MODELS\""; echo "ACCOUNT=\"$ACCOUNT\""; } > "$LOG_ROOT/run_config.env"
        cp "$HPC_DIR/config/models.tsv" "$LOG_ROOT/models.tsv"
    fi
    python3 "$HPC_DIR/scripts/manifest.py" --run-id "$RUN_ID" --hpc "$HPC_DIR" --config "$LOG_ROOT/run_config.env" \
        --out "$RUN_DIR/run_manifest.json" > /dev/null
    cp "$RUN_DIR/run_manifest.json" "$LOG_ROOT/run_manifest.json"
fi
OVERVIEW="$LOG_ROOT/pipeline_overview.log"
# messages go to stderr: submit() returns the job ID on stdout
say() { if $DRY; then echo "$*" >&2; else echo "$*" | tee -a "$OVERVIEW" >&2; fi; }

# ── sbatch wrapper ───────────────────────────────────────────────────────────
N=0
submit() {  # submit <name> <dependency or ""> <script> [extra sbatch args...] -- [extra exports]
    local name="$1" dep="$2" script="$3"; shift 3
    local args=() exports="ALL,HPC_DIR=$HPC_DIR,RUN_ID=$RUN_ID"
    while [[ $# -gt 0 && "$1" != "--" ]]; do args+=("$1"); shift; done
    [[ "${1:-}" == "--" ]] && shift
    for e in "$@"; do exports="$exports,$e"; done
    local cmd=(sbatch --parsable --account="$ACCOUNT" --job-name="$name"
               --output="$LOG_ROOT/slurm/%x_%j.out" --error="$LOG_ROOT/slurm/%x_%j.err"
               --export="$exports" "${args[@]}")
    [ -n "$dep" ] && cmd+=(--dependency="$dep" --kill-on-invalid-dep=yes)
    cmd+=("$HPC_DIR/slurm/$script")
    local id
    if $DRY; then id="<$name>"; echo "  ${cmd[*]}" >&2
    else id=$("${cmd[@]}"); id="${id%%;*}"; printf '%s\t%s\t%s\t%s\t%s\n' "$id" "$name" "$dep" "$script" "$*" >> "$LOG_ROOT/jobs.tsv"
    fi
    say "  $(printf '%-34s' "$name") job $id  ${dep:+(after $dep)}"
    echo "$id"
}
join() { local IFS=:; echo "$*"; }

say "============================================================"
say "AutoM2M EMSE study — RUN_ID $RUN_ID   ($(date))"
say "models:   $MODELS"
say "account:  $ACCOUNT    results: $RUN_DIR/results    logs: $LOG_ROOT"
say "============================================================"

SETUP_DEP=""
if ! $SKIP_SETUP && want setup; then
    J=$(submit am2m_00_setup "" 00_setup_models_and_data.sh)
    SETUP_DEP="afterok:$J"
fi

MATRIX=()
if want matrix; then
    for m in $MODELS; do
        for b in $BENCHES; do
            J=$(submit "am2m_10_matrix_$(safe "$m")_$b" "$SETUP_DEP" 10_rq3_condition_matrix.sh --time="$(model_field "$m" 4)" -- "MODEL=$m" "BENCH=$b")
            MATRIX+=("$J")
        done
    done
fi
AFTER_MATRIX=""
[ ${#MATRIX[@]} -gt 0 ] && AFTER_MATRIX="afterany:$(join "${MATRIX[@]}")"
# also require a successful setup, so that a failed setup cancels these jobs instead of releasing them
[ -n "$SETUP_DEP" ] && [ -n "$AFTER_MATRIX" ] && AFTER_MATRIX="$SETUP_DEP,$AFTER_MATRIX"
[ -z "$AFTER_MATRIX" ] && AFTER_MATRIX="$SETUP_DEP"

ALL=("${MATRIX[@]}")
if want rq2_builder; then ALL+=("$(submit am2m_20_rq2_builder "$SETUP_DEP" 20_rq1_audit_rq2_checker_builder.sh)"); fi
if want rq4_ref; then
    for m in $RQ4_REF_MODELS; do ALL+=("$(submit "am2m_21_rq4_ref_$(safe "$m")" "$SETUP_DEP" 21_rq4_fault_injection_reference_team.sh -- "MODEL=$m")"); done
fi
if want rq2_llm; then ALL+=("$(submit am2m_30_rq2_llm "$AFTER_MATRIX" 30_rq2_seeded_defects_vs_critics.sh)"); fi
if want rq4_builder; then
    ALL+=("$(submit "am2m_31_rq4_builder_$(safe "$RQ4_BUILDER_MODEL")" "$AFTER_MATRIX" 31_rq4_fault_injection_builder_teams.sh -- "MODEL=$RQ4_BUILDER_MODEL")")
fi
if want rq4_repair; then
    for m in $RQ4_REPAIR_MODELS; do ALL+=("$(submit "am2m_32_rq4_repair_$(safe "$m")" "$AFTER_MATRIX" 32_rq4_repair_vs_rebuild.sh -- "MODEL=$m")"); done
fi
if want coding; then ALL+=("$(submit am2m_33_coding "$AFTER_MATRIX" 33_rq1_rq3_rq4_failure_coding.sh)"); fi
if want analysis; then
    DEP=""; [ ${#ALL[@]} -gt 0 ] && DEP="afterany:$(join "${ALL[@]}")"
    [ -n "$SETUP_DEP" ] && [ -n "$DEP" ] && DEP="$SETUP_DEP,$DEP"
    [ -z "$DEP" ] && DEP="$SETUP_DEP"
    submit am2m_40_analysis "$DEP" 40_analyze_and_collect_results.sh > /dev/null
fi

say ""
say "Monitor (one call, no loops):   bash $HPC_DIR/status.sh $RUN_ID"
say "Job logs:                       $LOG_ROOT/slurm/"
say "Step logs and ledger:           $LOG_ROOT/steps/  $LOG_ROOT/steps.tsv"
say "Collected results (at the end): $LOG_ROOT/results/"
say "Audit (steps, substeps, timings): bash $HPC_DIR/status.sh $RUN_ID        [--watch 120 to refresh]"
if ! $DRY; then
    say ""
    if [ "$WATCH" -gt 0 ]; then
        exec python3 "$HPC_DIR/scripts/audit.py" "$RUN_ID" --watch "$WATCH"
    fi
    python3 "$HPC_DIR/scripts/audit.py" "$RUN_ID" >&2 || say "(audit unavailable; see status.sh)"
fi
