#!/bin/bash
#SBATCH --job-name=am2m_90_sizing
#SBATCH --time=01:30:00
#SBATCH --gpus=h100:1
#SBATCH --cpus-per-task=12
#SBATCH --mem=64G
#SBATCH --open-mode=append
#
# GPU memory sizing check (not part of the study graph). For every GPU stage it starts Ollama with that stage's
# OLLAMA_NUM_PARALLEL / OLLAMA_MAX_LOADED_MODELS, loads the models the stage loads with the stage's num_ctx, and
# reports how much of each model is in GPU memory (hpc/scripts/check_sizing.py). Anything that is not 100% on the
# GPU, or is evicted by a later model, would make a 24 h study job crawl. Result: logs/<RUN_ID>/sizing.txt
#   sbatch --account=<acc> --export=ALL,HPC_DIR=$PWD/hpc,RUN_ID=am2m-sizing hpc/slurm/90_check_gpu_sizing.sh
set -o pipefail
# shellcheck disable=SC1091
source "$HPC_DIR/slurm/common.sh"
setup_traps
print_job_header
setup_python || exit 1
OUT="$LOG_ROOT/sizing.txt"; : > "$OUT"
FAIL=0

first() { echo "${1%%@*}"; }
tags() { local s; for s in ${1//,/ }; do echo "${s%%@*}"; done | sort -u | tr '\n' ' '; }

scenario() {   # scenario <label> <num_parallel> <max_loaded> <num_ctx> <models...>
    local label="$1" par="$2" maxl="$3" ctx="$4"; shift 4
    {
        echo "== $label   (OLLAMA_NUM_PARALLEL=$par OLLAMA_MAX_LOADED_MODELS=$maxl num_ctx=$ctx)  models: $*"
    } | tee -a "$OUT"
    start_ollama "$par" "$maxl" > /dev/null || { echo "  ollama did not start" | tee -a "$OUT"; FAIL=1; return; }
    python "$HPC_DIR/scripts/check_sizing.py" --ctx "$ctx" --models "$@" 2>&1 | tee -a "$OUT"
    [ "${PIPESTATUS[0]}" -ne 0 ] && { FAIL=1; echo "  => PROBLEM" | tee -a "$OUT"; } || echo "  => ok" | tee -a "$OUT"
    kill "$OLLAMA_PID" 2>/dev/null; wait "$OLLAMA_PID" 2>/dev/null; OLLAMA_PID=""
}

JUDGE=$(first "$AM2M_JUDGE")
for m in $MODELS; do                                   # stage 10: main matrix, one model per job
    scenario "matrix $m" "$(model_field "$m" 2)" 1 16384 "$m"
done
scenario "rq2_llm seeder" "${AUX_PAR_RQ2LLM:-$AUX_NUM_PARALLEL}" "$AUX_MAX_LOADED" 32768 $(tags "$AM2M_SEEDER")
scenario "rq2_llm critics" "${AUX_PAR_RQ2LLM:-$AUX_NUM_PARALLEL}" "$AUX_MAX_LOADED" 32768 $(tags "$AM2M_CRITICS")
scenario "coding coders" "${AUX_PAR_CODING:-$AUX_NUM_PARALLEL}" 2 32768 $(tags "$AM2M_CODERS")
for m in $RQ4_REF_MODELS; do                           # stage 21
    scenario "rq4_ref $m + judge" "${AUX_PAR_RQ4REF:-$AUX_NUM_PARALLEL}" 2 32768 $(echo "$m $JUDGE" | tr ' ' '\n' | sort -u | tr '\n' ' ')
done
scenario "rq4_builder $RQ4_BUILDER_MODEL + judge" "$AUX_NUM_PARALLEL" 1 32768 $(echo "$RQ4_BUILDER_MODEL $JUDGE" | tr ' ' '\n' | sort -u | tr '\n' ' ')
for m in $RQ4_REPAIR_MODELS; do                        # stage 32
    scenario "rq4_repair $m" "$(model_field "$m" 2)" 1 16384 "$m"
done
echo; [ "$FAIL" -eq 0 ] && echo "SIZING: ALL SCENARIOS FIT" | tee -a "$OUT" || echo "SIZING: PROBLEMS FOUND (see above)" | tee -a "$OUT"
exit $FAIL
