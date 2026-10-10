#!/usr/bin/env bash
# A second, user-level Ollama server for the experiments: same model store as
# the system service, but with request batching (OLLAMA_NUM_PARALLEL), which
# raises aggregate throughput ~5x for 7B models. The harness talks to it via
# AM2M_OLLAMA (default http://127.0.0.1:11435).
set -u
PORT="${1:-11435}"
PAR="${2:-16}"
cd "$(dirname "$0")/../.."
mkdir -p results/logs
export OLLAMA_HOST="127.0.0.1:${PORT}" OLLAMA_MODELS="${OLLAMA_MODELS:-/usr/share/ollama/.ollama/models}" \
       OLLAMA_NUM_PARALLEL="$PAR" OLLAMA_MAX_LOADED_MODELS=1 OLLAMA_NOPRUNE=1 OLLAMA_KEEP_ALIVE=24h OLLAMA_FLASH_ATTENTION=1
exec ollama serve >> "results/logs/ollama${PORT}.log" 2>&1
