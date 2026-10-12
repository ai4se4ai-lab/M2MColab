#!/bin/bash
# Resume the Qwen 7B schedule after the builder-schema fix (builder.py: every
# property of TEAM_SCHEMA is required, so constrained decoding emits guards,
# validator args and goal anchors). Redo everything that uses builder-made
# typed teams: Typed-NC, AutoM2M (and the one-example variant), Single-Gate
# (its budget follows AutoM2M), the v2 half of the builder ablation, the RQ2
# studies on builder teams, all of RQ4; then phases 4-6 of run_qwen7b.sh.
# usage: setsid nohup evaluation/scripts/resume_after_schema.sh >> results/logs/run_qwen7b.log 2>&1 &
set -u
cd "$(dirname "$0")/../.."
PY=.venv/bin/python
M=qwen2.5-coder:7b
W=16
C=single,free,critic,schema,typed_nc,autom2m,single_gate,typed_ref
export AM2M_BUILDER="${AM2M_BUILDER:-v2}" HF_DATASETS_OFFLINE=1 PYTHONPATH=. AM2M_OLLAMA="${AM2M_OLLAMA:-http://127.0.0.1:11435}"
stamp() { echo "=== $(date '+%F %T') $*"; }
alive() { curl -s -m 5 "$AM2M_OLLAMA/api/version" > /dev/null; }
run() { until alive; do echo "--- $(date '+%T') waiting for $AM2M_OLLAMA"; sleep 60; done; "$@"; }
BK=results/backup/before_schema
mkdir -p "$BK"

stamp "resume: purge everything that used builder-made teams"
for b in classeval humanevalplus; do
  F=results/raw/${b}__qwen2.5-coder_7b.jsonl
  cp "$F" "$BK/"
  $PY - "$F" <<'PYEOF'
import json, sys
F = sys.argv[1]
AFFECTED = {"typed_nc", "autom2m", "autom2m_1ex", "single_gate"}
L = [l for l in open(F) if l.strip()]
keep = [l for l in L if not (json.loads(l)["condition"] in AFFECTED and json.loads(l)["seed"] == 1)]
open(F, "w").writelines(keep)
print(F, len(L), "->", len(keep))
PYEOF
  for c in typed_nc autom2m autom2m_1ex single_gate; do
    [ -d results/runs/$b/qwen2.5-coder_7b/$c ] && { mkdir -p "$BK/runs/$b/$c"; mv results/runs/$b/qwen2.5-coder_7b/$c/*__s1.json "$BK/runs/$b/$c/" 2>/dev/null; }
  done
done
A=results/rq2/builder_ablation__qwen2.5-coder_7b.jsonl
cp "$A" "$BK/"
$PY - "$A" <<'PYEOF'
import json, sys
F = sys.argv[1]
L = [l for l in open(F) if l.strip()]
keep = [l for l in L if json.loads(l).get("builder") != "v2"]
open(F, "w").writelines(keep)
print(F, len(L), "->", len(keep))
PYEOF
for f in seeded.jsonl clean.jsonl independent.jsonl natural_mutants.json; do [ -f results/rq2/$f ] && mv results/rq2/$f "$BK/"; done
mkdir -p "$BK/rq4"; mv results/rq4/* "$BK/rq4/" 2>/dev/null

stamp "resume: phase 1 again for the conditions with builder-made teams"
for b in classeval humanevalplus; do
  run $PY -m evaluation.run_matrix --bench $b --model "$M" --conditions typed_nc,autom2m --seeds 1 --workers "$W"
  run $PY -m evaluation.run_matrix --bench $b --model "$M" --conditions single_gate --seeds 1 --workers "$W"
done
run $PY -m evaluation.run_matrix --bench classeval --model "$M" --conditions autom2m_1ex --seeds 1 --workers "$W"
run $PY -m evaluation.rq2.builder_ablation --model "$M" --tasks first:40 --builders v2 --workers 12

stamp "resume: RQ2 studies on builder teams"
run $PY -m evaluation.rq2.natural_mutants
run $PY -m evaluation.rq2.independent seed --per-class 60 --workers "$W"
run $PY -m evaluation.rq2.independent clean
run $PY -m evaluation.rq2.independent critics --workers "$W"

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
