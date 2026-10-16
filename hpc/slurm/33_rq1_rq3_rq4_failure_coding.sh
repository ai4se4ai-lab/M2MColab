#!/bin/bash
#SBATCH --job-name=am2m_33_coding
#SBATCH --time=24:00:00
#SBATCH --nodes=1
#SBATCH --gpus=a100:2
#SBATCH --cpus-per-task=12
#SBATCH --mem=96G
#SBATCH --requeue
#SBATCH --open-mode=append
#SBATCH --signal=B:USR1@900
#SBATCH --mail-type=FAIL
#
# Stage 33 — LLM coding of failures (after the matrix), coders AM2M_CODERS
# (Qwen2.5-Coder 32B + Devstral, adjudication by the first): RQ4 natural failures
# (150), RQ1 decisive causes of the 184 Who&When failures and 400 Free/Critic
# failures, and RQ3 composition-caused failures of every failing executed run.
# Appends are resumable; the job requeues itself until done. Two GPUs.
set -o pipefail
# shellcheck disable=SC1091
source "$HPC_DIR/slurm/common.sh"
run_gpu_stage "${AUX_PAR_CODING:-$AUX_NUM_PARALLEL}" 2 coding
