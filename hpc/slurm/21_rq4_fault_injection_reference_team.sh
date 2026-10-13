#!/bin/bash
#SBATCH --job-name=am2m_21_rq4_ref
#SBATCH --time=24:00:00
#SBATCH --gpus=h100:2
#SBATCH --cpus-per-task=12
#SBATCH --mem=96G
#SBATCH --requeue
#SBATCH --open-mode=append
#SBATCH --signal=B:USR1@900
#SBATCH --mail-type=FAIL
#
# Stage 21 — RQ4 on the hand-written reference team for one subject model (3 jobs):
# 40 injected faults per class (5 classes) with trace-based attribution, then the four
# transcript-based baselines on the same runs (judge from AM2M_JUDGE). Two GPUs: the
# subject model and the 32B judge are resident together. Env: MODEL.
set -o pipefail
# shellcheck disable=SC1091
source "$HPC_DIR/slurm/common.sh"
: "${MODEL:?}"
run_gpu_stage "${AUX_PAR_RQ4REF:-$AUX_NUM_PARALLEL}" 2 rq4_ref --model "$MODEL"
