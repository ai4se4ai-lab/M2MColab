#!/bin/bash
# Stage every download of the study on a login / data-transfer node, for clusters whose compute nodes
# have no internet (Narval: "By policy, Narval's compute nodes cannot access the internet").
# Downloads only — no GPU work, no runs. Idempotent: re-run it to resume an interrupted download.
#
#   bash hpc/slurm/stage_offline.sh            # Ollama, wheels, Who&When, ClassEval/HumanEval+, nltk, all models
#   bash hpc/slurm/stage_offline.sh --models   # only the Ollama models
#
# The 00_setup job then finds no internet, checks that these files exist, and verifies them on a GPU.
set -uo pipefail
HPC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
REPO_DIR="$(cd "$HPC_DIR/.." && pwd)"
# shellcheck disable=SC1091
source "$HPC_DIR/config/pipeline.env"
log_info()  { echo "[$(date '+%F %T')] [INFO]  $*"; }
log_warn()  { echo "[$(date '+%F %T')] [WARN]  $*"; }
log_error() { echo "[$(date '+%F %T')] [ERROR] $*" >&2; }
# shellcheck disable=SC1091
source "$HPC_DIR/slurm/fetch.sh"
ONLY_MODELS=false; [ "${1:-}" = "--models" ] && ONLY_MODELS=true
TMPBASE="$WORK/tmp_stage"; mkdir -p "$TMPBASE"
export SLURM_TMPDIR="$TMPBASE"          # fetch.sh puts its temporary files here

if ! $ONLY_MODELS; then
    probe || { log_error "no internet on $(hostname)"; exit 1; }
    install_ollama || exit 1
    download_wheels || exit 1
    clone_whowhen || exit 1
    # datasets and nltk: a small venv from the wheelhouse (on the login node's local disk, reused by re-runs)
    module purge; module load $MODULES
    venv="${STAGE_VENV:-/tmp/$USER/am2m_stage_venv}"   # local disk: imports from a Lustre venv crawl on busy login nodes
    if [ ! -x "$venv/bin/python" ]; then
        virtualenv --no-download -q "$venv" || exit 1
        "$venv/bin/pip" install --no-index -q --upgrade pip
        "$venv/bin/pip" install --no-index -q datasets nltk requests || exit 1
    fi
    HF_HOME="$HF_HOME" NLTK_DATA_DIR="$NLTK_DATA_DIR" PYTHONPATH="$REPO_DIR:$REPO_DIR/src${PYTHONPATH:+:$PYTHONPATH}" \
        "$venv/bin/python" "$HPC_DIR/scripts/prefetch.py" download || exit 1
fi

# Models: a CPU-only Ollama server on this node, used only to download into $OLLAMA_MODELS
aux() { local s; for s in ${1//,/ }; do echo "${s%%@*}"; done; }
ALL_MODELS=$( { for m in $MODELS; do echo "$m"; done; aux "$AM2M_CODERS"; aux "$AM2M_CRITICS"; aux "$AM2M_SEEDER"; aux "$AM2M_JUDGE"; } | sort -u)
port=$(python3 -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1",0)); print(s.getsockname()[1]); s.close()')
export OLLAMA_HOST="127.0.0.1:$port" OLLAMA_MODELS CUDA_VISIBLE_DEVICES="" OLLAMA_NOPRUNE=1
mkdir -p "$OLLAMA_MODELS"
"$OLLAMA_PREFIX/bin/ollama" serve > "$TMPBASE/ollama_stage.log" 2>&1 &
OPID=$!
trap 'kill $OPID 2>/dev/null' EXIT
curl -sf --retry 30 --retry-delay 2 --retry-connrefused -m 5 "http://$OLLAMA_HOST/api/version" > /dev/null \
    || { log_error "Ollama did not start; see $TMPBASE/ollama_stage.log"; exit 1; }
rc=0
for m in $ALL_MODELS; do
    log_info "pulling $m into $OLLAMA_MODELS"
    ok=false
    for attempt in 1 2 3; do
        if "$OLLAMA_PREFIX/bin/ollama" pull "$m" 2>&1 | tr '\r' '\n' | grep -v -E '^pulling [0-9a-f]+: +[0-9]+%' | tail -3; then
            "$OLLAMA_PREFIX/bin/ollama" list | grep -q "^$m " && { ok=true; break; }
        fi
        log_warn "pull of $m failed (attempt $attempt)"
    done
    $ok || { log_error "could not pull $m"; rc=1; }
done
"$OLLAMA_PREFIX/bin/ollama" list
du -sh "$OLLAMA_MODELS"
[ $rc -eq 0 ] && log_info "STAGING COMPLETE" || log_error "STAGING INCOMPLETE"
exit $rc
