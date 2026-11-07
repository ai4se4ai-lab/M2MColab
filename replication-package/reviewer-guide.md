# AutoM2M frozen reviewer package

This is the evidence snapshot for the paper, frozen on
2026-11-07 from repository commit `431600a`. The package contains existing experiment evidence; packaging does not rerun LLM
experiments or alter the manuscript or original results.

## What is included

| Paper evidence | Frozen files | Code / protocol |
| --- | --- | --- |
| RQ1 reliability and task success | `results/raw/*.jsonl`, `results/data/runs.csv`, `paper-analysis/reliability*.csv` | `evaluation/run_matrix.py`, `evaluation/conditions/`, `paper-analysis/tools/reliability.py` |
| Who&When specification audit | `results/rq1/audit_whowhen.json`, `audit_matches.jsonl`, `data/Agents_Failure_Attribution/` | `evaluation/rq1/audit_whowhen.py` |
| RQ2 fault attribution, transcript comparisons, repairs | `results/rq4/`, `results/data/rq4_*.csv` | `evaluation/rq4/` |
| RQ3 seeded defects, independent critics, mutations, checker scaling, admission ablation | `results/rq2/`, `results/data/rq2_*.csv`, `results/data/admission.csv` | `evaluation/rq2/`, `teams/` |
| Historical failure coding and sensitivity analyses | `results/rq1/codes_*.jsonl`, `results/rq3/`, `results/data/*codes.csv` | `evaluation/rq1/`, `evaluation/rq3/` |
| Full main-study generated code, conversations, diagnostics and traces | `results/runs.tar.gz` (extracts to `runs/`) | `evaluation/harness/`, `src/` |
| Revised-method development pilot | `experiments/am2m-pilot-v3/` | `hpc/slurm/92_pilot_dev_subset.sh`, `docs/builder-v2.md` |
| Experiment 1 development budget sweep | `experiments/am2m-exp1-pilot/results/k{1,2,4,8}/`, `analysis/` | `hpc/slurm/93_budget_sweep.sh` |
| Experiment 1 held-out sweep, including post-hoc R1–R3 results | `experiments/am2m-exp1-confirm/results/k{1,2,4,8}/`, `analysis/exp1_{curves,tests}.csv` | `hpc/scripts/exp1_analyze.py` |
| Hypotheses and task splits | `hpc/docs/preregistration-exp1-budget-scaling.md`, `hpc/config/{dev,heldout}_tasks_*.txt` | Read the protocol before interpreting the sweep |
| Model digests, benchmark checks, environment, jobs, corrections | `evidence/main-setup/`, `hpc/logs/<run>/` | `hpc/README.md`, `docs/experiment-log.md`, per-run `CHANGES.md` |
| Original benchmark snapshots and dataset metadata | `benchmark-cache/` | Set `HF_HOME` as shown below; upstream dataset READMEs retain their license information |
| Source, fixtures and implementation tests | `src/`, `evaluation/`, `teams/`, `tests/`, `source-history.bundle` | `pyproject.toml`, `hpc/config/requirements.txt` |

RQ numbers in code follow an earlier paper structure: paper RQ2 maps to
`evaluation/rq4/`; paper RQ3 maps to `evaluation/rq2/`. The narrative and
TikZ/pgfplots figures in the manuscript are frozen as they stand. Some final
reliability outputs use the supplementary `paper-analysis/tools/reliability.py`,
so `evaluation.analysis.analyze` alone is not a complete manuscript build.

## Verify the evidence without installing dependencies

After extracting the ZIP, enter its top-level directory:

```sh
python verify_package.py
python packaging/audit_results.py . --out my-coverage-audit.json
```

The first command checks SHA-256 for every packaged payload file. The second
checks raw JSONL against the frozen CSV (including success and completion),
main-study cell coverage, task/seed coverage in both budget sweeps, and success
percentages against the frozen sweep curves. `coverage-audit.json` records the
packaging-time audit. `MANIFEST.json` records the source of every payload file,
its byte count and hash. The ZIP's adjacent `.sha256` file checks the ZIP itself.

There are **44,352 main-study runs**, across seven models, eight conditions,
three seeds and 264 tasks, and **100 additional `autom2m_1ex` ablation runs**.
Thus `results/data/runs.csv` contains 44,452 rows. Raw error/retry rows are
preserved; analysis uses the last non-error record per
(benchmark, model, task, condition, seed). The development budget sweep has
three recorded seeds; the written protocol originally specified one pilot seed.
The audit records what was actually collected. Pilot data do not form the
held-out confirmatory results.

## Recompute analyses without running models

Python 3.11 or newer is required. Create an environment and install the analysis
extras (installation may require internet); the exact original job environments
are retained in `hpc/logs/<run>/env/pip_freeze_*.txt`. Their cluster-specific
paths are provenance, not portable requirements files.

```sh
python -m venv .venv-review
. .venv-review/bin/activate
python -m pip install -e '.[eval]'
export PYTHONPATH="$PWD/src:$PWD"
export HF_HOME="$PWD/benchmark-cache"
export HF_DATASETS_OFFLINE=1 HF_HUB_OFFLINE=1
mkdir -p review-output/main/data review-output/reliability
cp results/data/*.csv review-output/main/data/
AM2M_RESULTS="$PWD/review-output/main" AM2M_DOCS_OUT="$PWD/review-output/main/docs_out" \
  python -m evaluation.analysis.analyze --no-export
python paper-analysis/tools/reliability.py "$PWD/results" "$PWD/review-output/reliability"
python hpc/scripts/exp1_analyze.py \
  --sweep "$PWD/experiments/am2m-exp1-confirm" --study "$PWD/results" \
  --tasks heldout --hpc "$PWD/hpc" --out "$PWD/review-output/heldout"
python hpc/scripts/exp1_analyze.py \
  --sweep "$PWD/experiments/am2m-exp1-pilot" --study "$PWD/results" \
  --tasks dev --hpc "$PWD/hpc" --out "$PWD/review-output/pilot"
AM2M_RESULTS="$PWD/review-output/audit" \
  python -m evaluation.rq1.audit_whowhen "$PWD/data/Agents_Failure_Attribution/Who&When"
```

For re-exporting CSVs from the full run records, work on a copy:

```sh
mkdir -p review-output/raw-reanalysis
cp -R results/. review-output/raw-reanalysis/
tar -xzf results/runs.tar.gz -C review-output/raw-reanalysis
AM2M_RESULTS="$PWD/review-output/raw-reanalysis" \
  python -m evaluation.analysis.export
```

These commands leave the frozen payload files unchanged. Recomputed statistical
outputs can vary with dependency versions. Packaging-time validation covers
integrity and recorded result agreement; it does not claim that every analysis
or LLM experiment was rerun during packaging.

## Provenance and limits

`FREEZE.json` pins the packaging commit and manuscript hash. The main study is
`am2m-20261014-015948`; its collected project copy is packaged at `results/`,
including the existing `runs.tar.gz`. Scratch results and the collected copy
are compared for shared non-run evidence during packaging. Experiment 1 and the
revision pilot are copied from their scratch run directories. HPC logs are
preserved for those four runs, including historical failed jobs and retries.

The checkout's current implementation includes post-hoc runtime revisions.
The original run manifest records a dirty working tree and corrections during
the study. `source-history.bundle` preserves the available repository history;
it does **not** reconstruct every uncommitted historical job state. Consult the
run manifest, job environment records and correction logs before attempting
new model runs. Recorded results remain the primary frozen evidence. The
pre-registration is the existing local protocol; this package does not supply
an independent registry timestamp or DOI.

Model weights, Ollama binaries, installed Python environments, wheelhouses,
unrelated projects, setup/sizing/smoke-only runs, and discarded/quarantined
experiments are excluded. Model identifiers/digests, setup checks and relevant
logs are included. Benchmark and Who&When files retain their upstream licenses;
no new license is asserted for third-party data. Git credentials, `.git/`, local
`.env`, Python caches and LaTeX temporary build files are excluded.