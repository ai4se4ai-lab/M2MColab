#!/bin/bash
#SBATCH --job-name=am2m_40_analysis
#SBATCH --time=04:00:00
#SBATCH --cpus-per-task=4
#SBATCH --mem=32G
#SBATCH --mail-type=FAIL,END
#
# Stage 40 — analysis and collection (CPU only; no LLM): every table, figure and number
# of the paper (evaluation/analysis), the multi-model report, per-model summaries
# (hpc/scripts/summarize.py, including completeness of every RQ), then copies the
# results from scratch to hpc/logs/<RUN_ID>/results/ (run files as one tar archive).
# Can be resubmitted at any time for an intermediate analysis:
#   bash hpc/slurm/submit_pipeline.sh --run-id <RUN_ID> --analysis-only
set -o pipefail
# shellcheck disable=SC1091
source "$HPC_DIR/slurm/common.sh"
setup_traps
print_job_header
setup_python || exit 1
run_stage analysis
