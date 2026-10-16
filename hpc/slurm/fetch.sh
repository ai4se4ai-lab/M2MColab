#!/bin/bash
# Download functions shared by the 00_setup job (clusters whose GPU nodes have internet, e.g. Nibi) and
# hpc/slurm/stage_offline.sh (clusters whose compute nodes have no internet, e.g. Narval: run on a login
# or data-transfer node). Each function is idempotent. Needs: pipeline.env sourced, log_info/log_warn/log_error.

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
    # Wheels missing from the Alliance wheelhouse (pyecore, python-docx and any of their dependencies the wheelhouse
    # lacks, e.g. future-fstrings on Narval): resolve requirements.txt offline; download each missing distribution
    # from PyPI; repeat until the offline install resolves.
    mkdir -p "$WHEELS"
    module purge; module load $MODULES
    local bootstrap="${SLURM_TMPDIR:-/tmp}/bootstrap" i missing
    [ -x "$bootstrap/bin/python" ] || virtualenv --no-download -q "$bootstrap" || return 1
    # shellcheck disable=SC1091
    source "$bootstrap/bin/activate"
    pip install --no-index -q --upgrade pip
    if ! ls "$WHEELS"/pyecore-*.whl "$WHEELS"/python_docx-*.whl >/dev/null 2>&1; then
        log_info "Not in the Alliance wheelhouse, downloading from PyPI: pyecore python-docx"
        pip download -q --no-deps --only-binary=:all: -d "$WHEELS" pyecore python-docx || { deactivate; return 1; }
    fi
    for i in 1 2 3 4 5 6 7 8 9 10; do
        missing=$(pip install --no-index --dry-run -q --find-links "$WHEELS" -r "$HPC_DIR/config/requirements.txt" 2>&1 \
                  | sed -n 's/.*No matching distribution found for \([A-Za-z0-9_.-]*\).*/\1/p' | head -1)
        [ -z "$missing" ] && break
        log_info "dependency missing from the wheelhouse, downloading from PyPI: $missing"
        pip download -q --no-deps --only-binary=:all: -d "$WHEELS" "$missing" \
            || pip download -q --no-deps -d "$WHEELS" "$missing" || { deactivate; return 1; }
    done
    deactivate
    log_info "extra wheels in $WHEELS: $(ls "$WHEELS" | tr '\n' ' ')"
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

