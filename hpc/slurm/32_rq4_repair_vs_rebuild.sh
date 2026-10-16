#!/bin/bash
#SBATCH --job-name=am2m_32_rq4_repair
#SBATCH --time=24:00:00
#SBATCH --nodes=1
#SBATCH --gpus=a100:1
#SBATCH --cpus-per-task=12
#SBATCH --mem=64G
#SBATCH --requeue
#SBATCH --open-mode=append
#SBATCH --signal=B:USR1@900
#SBATCH --mail-type=FAIL
#
# Stage 32 — RQ4 checked repair vs rebuilding on failed AutoM2M runs of one subject
# model (after the matrix; 3 jobs x RQ4_REPAIR_N runs). Env: MODEL.
set -o pipefail
# shellcheck disable=SC1091
source "$HPC_DIR/slurm/common.sh"
: "${MODEL:?}"
run_gpu_stage "$(model_field "$MODEL" 2)" 1 rq4_repair --model "$MODEL"
