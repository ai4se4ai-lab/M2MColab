#!/bin/bash
#SBATCH --job-name=am2m_30_rq2_llm
#SBATCH --time=24:00:00
#SBATCH --gpus=h100:2
#SBATCH --cpus-per-task=12
#SBATCH --mem=96G
#SBATCH --requeue
#SBATCH --open-mode=append
#SBATCH --signal=B:USR1@900
#SBATCH --mail-type=FAIL
#
# Stage 30 — RQ2 studies on builder-generated teams (after the matrix): detach operator
# on natural teams, 60 independently seeded defects per class (seeder AM2M_SEEDER),
# the clean set, and the checker vs three LLM critics (AM2M_CRITICS). Two GPUs: the
# critics are resident together.
set -o pipefail
# shellcheck disable=SC1091
source "$HPC_DIR/slurm/common.sh"
run_gpu_stage "${AUX_PAR_RQ2LLM:-$AUX_NUM_PARALLEL}" "$AUX_MAX_LOADED" rq2_llm
