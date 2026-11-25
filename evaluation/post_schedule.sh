#!/usr/bin/env bash
# Runs after evaluation/schedule.sh: static RQ2 studies, RQ3 baselines, RQ1 coding.
set -u
cd "$(dirname "$0")/.."
PY=.venv/bin/python
until grep -q SCHEDULE_DONE results/logs/schedule.log; do sleep 60; done
$PY -m evaluation.rq3.baselines_transcript --model qwen2.5-coder:7b --workers 5
$PY -m evaluation.rq2.independent generate --model qwen3.8:27b --per-cell 4
$PY -m evaluation.rq2.independent critic --model qwen2.5-coder:7b
$PY -m evaluation.rq2.independent critic --model qwen3.8:27b
$PY -m evaluation.rq1.code_failures --source whowhen --workers 2
$PY -m evaluation.rq1.code_failures --source ours --per-cell 25 --workers 2
$PY -m evaluation.rq3.inject --model qwen3.8:27b --tasks first:20 --workers 2
$PY -m evaluation.rq3.baselines_transcript --model qwen3.8:27b --workers 2
echo POST_DONE
