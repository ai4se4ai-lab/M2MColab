#!/bin/bash
#SBATCH --job-name=am2m_00_setup
#SBATCH --time=08:00:00
#SBATCH --nodes=1
#SBATCH --gpus=a100:1
#SBATCH --cpus-per-task=12
#SBATCH --mem=64G
#SBATCH --mail-type=FAIL,END
#
# Stage 00 — everything the study needs, prepared on a GPU node (idempotent):
#   1. connectivity probe (GitHub, Ollama registry, Hugging Face, PyPI)
#   2. Ollama $OLLAMA_VERSION (bin + GPU runners) into $OLLAMA_PREFIX
#   3. wheels missing from the Alliance wheelhouse (pyecore, python-docx) into $WHEELS
#   4. Who&When clone into $AM2M_DATA
#   5. venv, private Ollama server, then hpc/scripts/run_pipeline.py setup:
#      datasets + nltk data, offline verification (reference solutions pass the hidden
#      tests in the sandbox), pull missing models, 1-token GPU check of every model,
#      and a pre-flight of the harness (1 task per benchmark, Single + AutoM2M).
# Every other stage depends on this job with afterok: if anything here fails, nothing runs.

set -o pipefail
# shellcheck disable=SC1091
source "$HPC_DIR/slurm/common.sh"
setup_traps
print_job_header

# shellcheck disable=SC1091
source "$HPC_DIR/slurm/fetch.sh"

check_staged() {   # offline clusters: everything must have been staged by hpc/slurm/stage_offline.sh
    local ok=0 f
    for f in "$OLLAMA_PREFIX/bin/ollama" "$AM2M_DATA/Agents_Failure_Attribution/Who&When" "$HF_HOME" "$OLLAMA_MODELS/manifests"; do
        if [ -e "$f" ]; then log_info "staged: $f"; else log_error "NOT staged: $f"; ok=1; fi
    done
    ls "$WHEELS"/pyecore-*.whl "$WHEELS"/python_docx-*.whl >/dev/null 2>&1 || { log_error "NOT staged: wheels in $WHEELS"; ok=1; }
    return $ok
}

main() {
    if probe; then
        install_ollama || return 1
        download_wheels || return 1
        clone_whowhen || return 1
    else
        log_warn "This GPU node has no internet access (expected on Narval): using the files staged on the login node"
        check_staged || { log_error "Run 'bash hpc/slurm/stage_offline.sh' on a login node first (see hpc/README.md)."; return 1; }
        export SETUP_OFFLINE=1      # run_pipeline.py: no dataset download, no model pulls (only presence checks)
    fi
    if ! setup_python; then
        log_warn "venv install failed with --no-deps wheels; downloading their dependencies too"
        rm -rf "${SLURM_TMPDIR:-/tmp}/venv"
        module purge; module load $MODULES
        virtualenv --no-download -q "${SLURM_TMPDIR:-/tmp}/dl" && source "${SLURM_TMPDIR:-/tmp}/dl/bin/activate"
        pip download -q --only-binary=:all: -d "$WHEELS" pyecore python-docx || return 1
        deactivate
        setup_python || return 1
    fi
    start_ollama 4 1 || return 1
    start_gpu_monitor
    run_stage setup
}

main
