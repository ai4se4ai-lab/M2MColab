#!/bin/bash
#SBATCH --job-name=am2m_20_rq2_builder
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
# Stage 20 — RQ1 audit and RQ2 studies that need no matrix data (Qwen2.5-Coder 7B):
# mutation analysis (Tab. mutation), checker cost (Fig. scale a/b), Who&When
# specification audit (Tab. 1), builder ablation v1 vs v2 on 40 ClassEval tasks, and
# the one-worked-example AutoM2M configuration (example copying).
set -o pipefail
# shellcheck disable=SC1091
source "$HPC_DIR/slurm/common.sh"
run_gpu_stage "$(model_field "$RQ2_BUILDER_MODEL" 2)" 1 rq2_builder
