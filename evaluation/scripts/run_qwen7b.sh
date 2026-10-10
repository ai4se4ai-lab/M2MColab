#!/usr/bin/env bash
# The whole study for one subject model (default qwen2.5-coder:7b), in an
# order that gives every research question data early: seed 1 of the full
# matrix, then the studies that build on it, then seeds 2 and 3, then the
# coding of the new failures and the analysis. Every step is resumable: rerun
# the script after an interruption and finished work is skipped.
#
#   evaluation/scripts/ollama_parallel.sh 11435 16 &     # batching Ollama server
#   nohup evaluation/scripts/run_qwen7b.sh > results/logs/run_qwen7b.log 2>&1 &
#
# Auxiliary roles (coders, critics, seeder, judge) use the same model unless
# AM2M_CODERS / AM2M_CRITICS / AM2M_SEEDER / AM2M_JUDGE say otherwise.
set -u
cd "$(dirname "$0")/../.."
export HF_DATASETS_OFFLINE=1 PYTHONPATH=. AM2M_OLLAMA="${AM2M_OLLAMA:-http://127.0.0.1:11435}"
PY=.venv/bin/python
M="${MODEL:-qwen2.5-coder:7b}"
W="${WORKERS:-16}"
C=single,free,critic,schema,typed_nc,autom2m,typed_ref,single_gate
stamp() { echo "=== $(date '+%F %T') $*"; }
# every step waits for a healthy LLM server, so an outage pauses the study instead of failing it
alive() { curl -s -m 5 "$AM2M_OLLAMA/api/version" > /dev/null; }
run() { until alive; do echo "--- $(date '+%T') waiting for $AM2M_OLLAMA"; sleep 60; done; "$@"; }

stamp "phase 1: matrix, seed 1"
run $PY -m evaluation.run_matrix --bench classeval --model "$M" --conditions $C --seeds 1 --workers "$W"
run $PY -m evaluation.run_matrix --bench humanevalplus --model "$M" --conditions $C --seeds 1 --workers "$W"

stamp "phase 2: RQ2 checker studies"
run $PY -m evaluation.rq2.mutate
run $PY -m evaluation.rq2.scale
run $PY -m evaluation.rq2.natural_mutants
run $PY -m evaluation.rq2.independent seed --per-class 60 --workers "$W"
run $PY -m evaluation.rq2.independent clean
run $PY -m evaluation.rq2.independent critics --workers "$W"
run $PY -m evaluation.run_matrix --bench classeval --model "$M" --conditions autom2m_1ex --seeds 1 --workers "$W"

stamp "phase 3: RQ4 attribution and repair"
run $PY -m evaluation.rq4.inject --team ref --model "$M" --per-class 40 --workers 12
run $PY -m evaluation.rq4.inject --team builder --model "$M" --per-class 40 --workers 12
run $PY -m evaluation.rq4.transcript --source ref --model "$M" --workers "$W"
run $PY -m evaluation.rq4.transcript --source builder --model "$M" --workers "$W"
run $PY -m evaluation.rq4.natural --n 150 --workers "$W"
run $PY -m evaluation.rq4.repair --model "$M" --n 150 --workers 12

stamp "phase 4: RQ1 coding and RQ3 coding of seed 1"
run $PY -m evaluation.rq1.audit_whowhen
run $PY -m evaluation.rq1.code_failures --source whowhen --workers "$W"
run $PY -m evaluation.rq1.code_failures --source ours --n 400 --workers "$W"
run $PY -m evaluation.rq3.code_runs --workers "$W"
run $PY -m evaluation.analysis.analyze && $PY -m evaluation.analysis.web_export && $PY -m evaluation.analysis.report

stamp "phase 5: matrix, seeds 2 and 3"
run $PY -m evaluation.run_matrix --bench classeval --model "$M" --conditions $C --seeds 2,3 --workers "$W"
run $PY -m evaluation.run_matrix --bench humanevalplus --model "$M" --conditions $C --seeds 2,3 --workers "$W"

stamp "phase 6: coding of the new failures, analysis"
run $PY -m evaluation.rq3.code_runs --workers "$W"
run $PY -m evaluation.rq2.natural_mutants
run $PY -m evaluation.analysis.analyze && $PY -m evaluation.analysis.web_export && $PY -m evaluation.analysis.report
stamp "ALL DONE"
