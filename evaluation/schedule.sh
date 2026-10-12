#!/usr/bin/env bash
# Sequential schedule (one model on the GPU at a time). Budget deviation: the 27B
# runs one seed on 40 ClassEval and 41 HumanEval+ tasks (see paper, Sec. threats).
set -u
cd "$(dirname "$0")/.."
PY=.venv/bin/python
C=single,free,critic,schema,typed_unchecked,autom2m,typed_ref
$PY -m evaluation.run_matrix --bench classeval --model qwen3.8:27b --conditions $C --seeds 1 --tasks file:evaluation/ce_40.txt --workers 2
$PY -m evaluation.run_matrix --bench classeval --model qwen2.5-coder:7b --conditions $C --seeds 1,2,3 --workers 6
$PY -m evaluation.run_matrix --bench humanevalplus --model qwen2.5-coder:7b --conditions $C --seeds 1,2 --workers 6
$PY -m evaluation.rq3.inject --model qwen2.5-coder:7b --tasks first:60 --workers 5
$PY -m evaluation.run_matrix --bench humanevalplus --model qwen3.8:27b --conditions $C --seeds 1 --tasks file:evaluation/he_quarter.txt --workers 2
echo SCHEDULE_DONE
