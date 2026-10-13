#!/bin/bash
#SBATCH --job-name=am2m_10_matrix
#SBATCH --time=24:00:00
#SBATCH --gpus=h100:1
#SBATCH --cpus-per-task=12
#SBATCH --mem=64G
#SBATCH --requeue
#SBATCH --open-mode=append
#SBATCH --signal=B:USR1@900
#SBATCH --mail-type=FAIL
#
# Stage 10 — RQ2/RQ3 main matrix for one subject model and one benchmark (14 such jobs):
# run_matrix over the 8 conditions (Single, Free, Critic, Schema, Typed-NC, AutoM2M,
# Typed-Ref, then Single-Gate whose budget is AutoM2M's median tokens) x all tasks x
# seeds 1,2,3. Resumable: finished runs are skipped; up to MATRIX_PASSES passes retry
# runs that ended in "error"; the job requeues itself at its time limit until complete.
# Env (from submit_pipeline.sh): MODEL, BENCH.
set -o pipefail
# shellcheck disable=SC1091
source "$HPC_DIR/slurm/common.sh"
: "${MODEL:?}" "${BENCH:?}"
run_gpu_stage "$(model_field "$MODEL" 2)" 1 matrix --model "$MODEL" --bench "$BENCH"
