#!/bin/bash
#SBATCH --job-name=am2m_91_gen
#SBATCH --time=01:30:00
#SBATCH --nodes=1
#SBATCH --gpus=a100:2
#SBATCH --cpus-per-task=24
#SBATCH --mem=240G
#SBATCH --open-mode=append
#
# Generation-throughput check for one model (not part of the study graph). The sizing job (90) only checks that a
# model loads onto the GPU; this one also generates, with concurrent requests, under several server settings
# (GPU count, OLLAMA_NUM_PARALLEL, flash attention), and reports tokens/s per setting. Result: logs/<RUN_ID>/generation.txt
#   sbatch --account=<acc> --export=ALL,HPC_DIR=$PWD/hpc,RUN_ID=am2m-gen,MODEL=gemma3:27b hpc/slurm/91_check_model_generation.sh
set -o pipefail
# shellcheck disable=SC1091
source "$HPC_DIR/slurm/common.sh"
: "${MODEL:?}"
setup_traps
print_job_header
OUT="$LOG_ROOT/generation.txt"; : > "$OUT"
ALL_GPUS="${CUDA_VISIBLE_DEVICES:-0}"
BIN="$OLLAMA_PREFIX/bin/ollama"
PROMPT='Write a Python class LRUCache with get(key) and put(key, value) methods and O(1) operations. Include docstrings and a short usage example.'

trial() {   # trial <gpus> <num_parallel> <flash 0|1> <concurrent requests>
    local ngpu="$1" par="$2" fa="$3" conc="$4" port i
    port=$(python3 -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1",0)); print(s.getsockname()[1]); s.close()')
    local url="http://127.0.0.1:$port" log="$LOG_ROOT/ollama/gen_${SLURM_JOB_ID}_g${ngpu}_p${par}_fa${fa}.log"
    CUDA_VISIBLE_DEVICES="$(echo "$ALL_GPUS" | cut -d, -f1-"$ngpu")" OLLAMA_HOST="127.0.0.1:$port" OLLAMA_MODELS="$OLLAMA_MODELS" \
        OLLAMA_NUM_PARALLEL="$par" OLLAMA_MAX_LOADED_MODELS=1 OLLAMA_FLASH_ATTENTION="$fa" OLLAMA_KEEP_ALIVE=1h \
        "$BIN" serve > "$log" 2>&1 &
    local pid=$!
    curl -sf --retry 60 --retry-delay 2 --retry-connrefused -m 5 "$url/api/version" > /dev/null || { echo "  server did not start" | tee -a "$OUT"; kill $pid; return; }
    local t0=$SECONDS
    curl -s -m 600 "$url/api/generate" -d "{\"model\":\"$MODEL\",\"prompt\":\"\",\"keep_alive\":\"1h\",\"options\":{\"num_ctx\":16384}}" > /dev/null
    local load=$((SECONDS - t0)) share
    share=$(curl -s "$url/api/ps" | python3 -c 'import json,sys; m=json.load(sys.stdin)["models"]; print(" ".join(f"{x[\"size_vram\"]/x[\"size\"]:.0%}" for x in m) or "not-loaded")')
    t0=$SECONDS
    for i in $(seq 1 "$conc"); do
        curl -s -m 600 "$url/api/generate" -d "{\"model\":\"$MODEL\",\"prompt\":\"$PROMPT\",\"stream\":false,\"options\":{\"num_ctx\":16384,\"num_predict\":256,\"temperature\":0}}" \
            > "$TMPDIR/gen_$i.json" &
    done
    wait $(jobs -p | grep -v "^$pid$") 2>/dev/null
    local wall=$((SECONDS - t0))
    python3 - "$TMPDIR" "$conc" "$wall" "$ngpu" "$par" "$fa" "$load" "$share" <<'EOF' | tee -a "$OUT"
import json, sys, glob
d, conc, wall, ngpu, par, fa, load, share = sys.argv[1:]
toks, rates, errs = 0, [], 0
for i in range(1, int(conc) + 1):
    try:
        r = json.load(open(f"{d}/gen_{i}.json"))
        toks += r.get("eval_count", 0)
        if r.get("eval_duration"):
            rates.append(r["eval_count"] / (r["eval_duration"] / 1e9))
        if "error" in r or not r.get("eval_count"):
            errs += 1
    except Exception:
        errs += 1
agg = toks / max(int(wall), 1)
print(f"gpus={ngpu} num_parallel={par} flash={fa} concurrent={conc}: load {load}s gpu={share} | {toks} tokens in {wall}s "
      f"= {agg:.1f} tok/s aggregate, per-request {sum(rates)/len(rates) if rates else 0:.1f} tok/s, failed {errs}/{conc}")
EOF
    kill $pid; wait $pid 2>/dev/null
}

module purge; module load $MODULES
export TMPDIR="${SLURM_TMPDIR:-/tmp}"
echo "== $MODEL generation check on $(hostname) ($(nvidia-smi --query-gpu=name --format=csv,noheader | head -1))" | tee -a "$OUT"
trial 2 8 1 8      # current matrix setting
trial 2 8 0 8      # flash attention off
trial 1 1 1 1      # single request baseline (what 00_setup verified)
trial 1 4 1 4
trial 1 4 0 4
trial 1 6 0 6
trial 2 8 0 1      # single request, 2 GPUs, no flash attention
