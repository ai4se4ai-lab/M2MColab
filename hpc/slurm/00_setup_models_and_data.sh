#!/bin/bash
#SBATCH --job-name=am2m_00_setup
#SBATCH --time=08:00:00
#SBATCH --gpus=h100:1
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

probe() {
    local ok=0 url
    for url in https://github.com https://registry.ollama.ai/v2/ https://huggingface.co https://pypi.org/simple/pip/; do
        local code
        code=$(curl -s -o /dev/null -m 20 -w '%{http_code}' "$url")
        if [ -n "$code" ] && [ "$code" != "000" ]; then   # any HTTP answer (the registry root answers 404)
            log_info "reachable: $url (HTTP $code)"
        else
            log_error "NOT reachable from $(hostname): $url"; ok=1
        fi
    done
    return $ok
}

install_ollama() {
    if [ -x "$OLLAMA_PREFIX/bin/ollama" ] && [ -d "$OLLAMA_PREFIX/lib/ollama" ]; then
        log_info "Ollama $OLLAMA_VERSION already installed at $OLLAMA_PREFIX"
        return 0
    fi
    local tmp="${SLURM_TMPDIR:-/tmp}/ollama_dl" base="https://github.com/ollama/ollama/releases/download/v$OLLAMA_VERSION"
    mkdir -p "$tmp" "$OLLAMA_PREFIX"
    if curl -fsSL --retry 3 "$base/ollama-linux-amd64.tar.zst" -o "$tmp/ollama.tar.zst"; then
        tar --use-compress-program=zstd -xf "$tmp/ollama.tar.zst" -C "$OLLAMA_PREFIX" || return 1
    elif curl -fsSL --retry 3 "$base/ollama-linux-amd64.tgz" -o "$tmp/ollama.tgz"; then
        tar -xzf "$tmp/ollama.tgz" -C "$OLLAMA_PREFIX" || return 1
    else
        log_error "Could not download Ollama $OLLAMA_VERSION from $base"; return 1
    fi
    rm -rf "$tmp"
    [ -x "$OLLAMA_PREFIX/bin/ollama" ] && [ -d "$OLLAMA_PREFIX/lib/ollama" ] || { log_error "unexpected Ollama archive layout"; return 1; }
    log_info "Installed Ollama: $("$OLLAMA_PREFIX/bin/ollama" --version 2>&1 | tail -1)"
}

download_wheels() {
    mkdir -p "$WHEELS"
    if ls "$WHEELS"/pyecore-*.whl "$WHEELS"/python_docx-*.whl >/dev/null 2>&1; then
        log_info "extra wheels already in $WHEELS"; return 0
    fi
    module purge; module load $MODULES
    local bootstrap="${SLURM_TMPDIR:-/tmp}/bootstrap"
    virtualenv --no-download -q "$bootstrap" && source "$bootstrap/bin/activate"
    pip install --no-index -q --upgrade pip
    log_info "Not in the Alliance wheelhouse, downloading from PyPI: pyecore python-docx"
    pip download -q --no-deps --only-binary=:all: -d "$WHEELS" pyecore python-docx || return 1
    deactivate
    ls "$WHEELS"
}

clone_whowhen() {
    local dest="$AM2M_DATA/Agents_Failure_Attribution"
    if [ -d "$dest/Who&When" ]; then
        log_info "Who&When present: $dest ($(git -C "$dest" rev-parse --short HEAD 2>/dev/null))"; return 0
    fi
    mkdir -p "$AM2M_DATA"
    git clone --depth 1 https://github.com/ag2ai/Agents_Failure_Attribution "$dest" || return 1
    log_info "Who&When cloned at $(git -C "$dest" rev-parse --short HEAD)"
}

main() {
    probe || { log_error "This GPU node has no internet access; the setup stage cannot download. Contact Alliance support or stage the files manually (see hpc/README.md)."; return 1; }
    install_ollama || return 1
    download_wheels || return 1
    clone_whowhen || return 1
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
