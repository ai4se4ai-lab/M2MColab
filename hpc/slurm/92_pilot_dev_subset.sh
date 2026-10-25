#!/bin/bash
#SBATCH --job-name=am2m_92_pilot
#SBATCH --time=08:00:00
#SBATCH --nodes=1
#SBATCH --gpus=a100:1
#SBATCH --cpus-per-task=12
#SBATCH --mem=120G
#SBATCH --open-mode=append
#
# Pilot of a method revision on the development subset (not part of the study graph): the given conditions, seed 1,
# on hpc/config/dev_tasks_<bench>.txt (a seeded 20% of the tasks; the other 80% are held out), for one model, written
# to its own results directory. Compare with the study run's rows for the same tasks and seed.
#   sbatch --account=<acc> --gpus=a100:<n> --export=ALL,HPC_DIR=$PWD/hpc,RUN_ID=am2m-pilot-v3,MODEL=qwen2.5-coder:7b,\
#          CONDS=autom2m hpc/slurm/92_pilot_dev_subset.sh   (one condition per job: --export splits on commas)
set -o pipefail
# shellcheck disable=SC1091
source "$HPC_DIR/slurm/common.sh"
: "${MODEL:?}"
CONDS="${CONDS:-typed_ref,autom2m}"
setup_traps
print_job_header
setup_python || exit 1
start_ollama "$(model_field "$MODEL" 2)" 1 || exit 1
start_gpu_monitor
W="$(model_field "$MODEL" 3)"
cd "$REPO_DIR" || exit 1
for b in classeval humanevalplus; do
    log_info "pilot $MODEL $b: $CONDS on $(wc -l < "$HPC_DIR/config/dev_tasks_$b.txt") dev tasks"
    setsid python -m evaluation.run_matrix --bench "$b" --model "$MODEL" --conditions "$CONDS" --seeds 1 \
        --tasks "file:$HPC_DIR/config/dev_tasks_$b.txt" --workers "$W" > "$LOG_ROOT/steps/pilot_${MODEL//[:\/]/_}_$b.log" 2>&1 &
    STAGE_PID=$!
    wait "$STAGE_PID"
    STAGE_PID=""
done
