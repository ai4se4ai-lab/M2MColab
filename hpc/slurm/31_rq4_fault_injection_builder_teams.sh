#!/bin/bash
#SBATCH --job-name=am2m_31_rq4_builder
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
# Stage 31 — RQ4 on builder-generated admitted teams (Qwen2.5-Coder 32B, after the
# matrix): 40 injected faults per class plus the transcript baselines. Env: MODEL.
set -o pipefail
# shellcheck disable=SC1091
source "$HPC_DIR/slurm/common.sh"
: "${MODEL:?}"
run_gpu_stage "$AUX_NUM_PARALLEL" 1 rq4_builder --model "$MODEL"
