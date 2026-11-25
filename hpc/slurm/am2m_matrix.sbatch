#!/usr/bin/env bash
# AutoM2M experiment job: one model, one benchmark, one shard of tasks, node-local Ollama on one GPU.
# Required env (sbatch --export): AM2M_WORK MODEL BENCH CONDITIONS SEEDS TASKFILE TAG WORKERS PARALLEL
#                                 OLLAMA_BIN OLLAMA_MODELS   optional: NUM_CTX GATE_BUDGET STUDY
#SBATCH --job-name=am2m
#SBATCH --cpus-per-task=16
#SBATCH --mem=96G
#SBATCH --output=%x-%j.out
set -euo pipefail
module load StdEnv/2023 python/3.12 2>/dev/null || true
echo "host=$(hostname) job=$SLURM_JOB_ID model=$MODEL bench=$BENCH tag=$TAG gpu=$(nvidia-smi --query-gpu=name,memory.total --format=csv,noheader | head -1)"
W="$AM2M_WORK"
LOG="$W/logs/ollama_${SLURM_JOB_ID}.log"
mkdir -p "$W/logs" "$W/results/$TAG"

# Ollama runtime on node-local disk (shared scratch can trip Ollama's GPU discovery watchdog)
runtime_source=$(cd "$(dirname "$OLLAMA_BIN")/.." && pwd)
runtime_local="${SLURM_TMPDIR:-/tmp}/am2m-ollama"
mkdir -p "$runtime_local"
rsync -a --exclude=vulkan "$runtime_source/" "$runtime_local/"
export OLLAMA_VULKAN=0 OLLAMA_LOAD_TIMEOUT=10m OLLAMA_NOPRUNE=1 OLLAMA_NO_CLOUD=1
export OLLAMA_HOST="127.0.0.1:$((20000 + SLURM_JOB_ID % 30000))"
export OLLAMA_NUM_PARALLEL="${PARALLEL:-16}" OLLAMA_MAX_LOADED_MODELS=1 OLLAMA_KEEP_ALIVE=24h
export OLLAMA_MODELS OLLAMA_CONTEXT_LENGTH="${NUM_CTX:-16384}" OLLAMA_FLASH_ATTENTION=1
start_ollama() {
  "$runtime_local/bin/ollama" serve >> "$LOG" 2>&1 &
  SRV=$!
  curl --fail --silent --retry 30 --retry-connrefused --retry-delay 2 --retry-max-time 120 "http://$OLLAMA_HOST/api/version"; echo
  curl -s "http://$OLLAMA_HOST/api/generate" -d "{\"model\":\"$MODEL\",\"prompt\":\"\",\"keep_alive\":\"24h\",\"options\":{\"num_ctx\":${NUM_CTX:-16384}}}" > /dev/null
  "$W/venv/bin/python" - "$OLLAMA_HOST" "$MODEL" <<'PY'
import json, sys, urllib.request
ps = json.load(urllib.request.urlopen(f"http://{sys.argv[1]}/api/ps"))
m = [x for x in ps.get("models", []) if x["name"].startswith(sys.argv[2])]
assert m, f"model not loaded: {ps}"
size, vram = m[0]["size"], m[0].get("size_vram", 0)
print(f"residency size={size} vram={vram} ratio={vram/size:.3f} digest={m[0].get('digest','')[:16]}")
sys.exit(0 if vram >= 0.98 * size else 1)
PY
}
trap 'kill $SRV 2>/dev/null || true' EXIT
ok=0
for attempt in 1 2 3; do
  if start_ollama; then ok=1; break; fi
  echo "attempt $attempt: model not GPU resident, restarting Ollama"; kill $SRV 2>/dev/null || true; sleep 15
done
[ "$ok" = 1 ] || { echo "model not fully GPU resident after 3 attempts"; exit 3; }

# the experiment: AutoM2M code, exported benchmarks, sandbox interpreter, results per tag
export AM2M_OLLAMA="http://$OLLAMA_HOST" AM2M_RESULTS="$W/results/$TAG" AM2M_DATA_DIR="$W/code/data/benchmarks"
export AM2M_SANDBOX_PY="$W/venv-sbx/bin/python" NLTK_DATA="$W/nltk_data" HF_DATASETS_OFFLINE=1
export PYTHONPATH="$W/code/src:$W/code" TMPDIR="${SLURM_TMPDIR:-/tmp}" AM2M_BUILDER="${BUILDER:-v3}"
[ -n "${GATE_BUDGET:-}" ] && export AM2M_GATE_BUDGET_FIXED="$GATE_BUDGET"
cd "$W/code"
if [ -n "${STUDY:-}" ]; then
  # an RQ study module instead of the matrix (its arguments in STUDY_ARGS)
  "$W/venv/bin/python" -m "$STUDY" ${STUDY_ARGS:-}
else
  "$W/venv/bin/python" -m evaluation.run_matrix --bench "$BENCH" --model "$MODEL" --conditions "${CONDITIONS//+/,}" \
    --seeds "${SEEDS//:/,}" --tasks "file:$W/code/$TASKFILE" --workers "${WORKERS:-16}" --tag "${SHARD_TAG:-}"
fi
nvidia-smi --query-gpu=name,utilization.gpu,memory.used --format=csv,noheader
echo "done $(date -u +%FT%TZ)"
