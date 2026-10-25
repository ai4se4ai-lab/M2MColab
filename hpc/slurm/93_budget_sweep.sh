#!/bin/bash
#SBATCH --job-name=am2m_93_sweep
#SBATCH --time=24:00:00
#SBATCH --nodes=1
#SBATCH --gpus=a100:1
#SBATCH --cpus-per-task=12
#SBATCH --mem=120G
#SBATCH --requeue
#SBATCH --open-mode=append
#SBATCH --signal=B:USR1@900
#
# Experiment 1 (hpc/docs/preregistration-exp1-budget-scaling.md): AutoM2M at k = KS samples per binding, each level
# in its own results directory (results/k<K>), then Single-Gate token-matched to that level. Resumable (finished runs are
# skipped); requeues itself at the time limit.
#   sbatch --account=<acc> --gpus=a100:<n> --export=ALL,HPC_DIR=$PWD/hpc,RUN_ID=am2m-exp1-pilot,MODEL=qwen2.5-coder:7b,\
#          BENCH=classeval,EXP1_TASKS=dev,EXP1_SEEDS=1 hpc/slurm/93_budget_sweep.sh   (EXP1_TASKS: dev | heldout)
set -o pipefail
# shellcheck disable=SC1091
source "$HPC_DIR/slurm/common.sh"
: "${MODEL:?}" "${BENCH:?}"
# EXP1_* names: pipeline.env (sourced by common.sh) defines TASKS and SEEDS and would override plain names
TASKS="${EXP1_TASKS:-dev}"; KS="${EXP1_KS:-1 2 4 8}"
# seeds as in the pre-registration: 1 on the development subset, 1-3 on the held-out tasks (a comma list cannot be
# passed through sbatch --export, which splits on commas)
if [ "$TASKS" = heldout ]; then SEEDS="${EXP1_SEEDS:-1,2,3}"; else SEEDS="${EXP1_SEEDS:-1}"; fi
setup_traps
print_job_header
setup_python || exit 1
start_ollama "$(model_field "$MODEL" 2)" 1 || exit 1
start_gpu_monitor
W="$(model_field "$MODEL" 3)"
TF="$HPC_DIR/config/${TASKS}_tasks_$BENCH.txt"
if [ "$TASKS" = heldout ] && [ ! -s "$TF" ]; then
    python - "$BENCH" "$HPC_DIR/config/dev_tasks_$BENCH.txt" "$TF" <<'PY'
import sys
from evaluation.benchmarks import tasks as T
dev = {l.strip() for l in open(sys.argv[2]) if l.strip()}
open(sys.argv[3], "w").write("\n".join(t.task_id for t in T.load(sys.argv[1]) if t.task_id not in dev) + "\n")
PY
fi
cd "$REPO_DIR" || exit 1
for K in $KS; do
    log_info "Experiment 1: $MODEL $BENCH k=$K ($TASKS tasks, seeds $SEEDS)"
    # one results directory per level: the full per-run records (runs/...) are not tagged and would collide
    AM2M_K="$K" AM2M_RESULTS="$RUN_DIR/results/k$K" setsid python -m evaluation.run_matrix --bench "$BENCH" \
        --model "$MODEL" --conditions autom2m,single_gate --seeds "$SEEDS" --tasks "file:$TF" --workers "$W" \
        >> "$LOG_ROOT/steps/exp1_${MODEL//[:\/]/_}_${BENCH}_k$K.log" 2>&1 &
    STAGE_PID=$!
    wait "$STAGE_PID" || log_warn "k=$K ended with an error (see the step log)"
    STAGE_PID=""
done
