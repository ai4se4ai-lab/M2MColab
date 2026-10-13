# Running the AutoM2M EMSE study on Nibi (Slurm)

This directory runs every experiment of `docs/emse-autom2m.tex` §5 on the Alliance cluster **Nibi**.
That covers RQ1–RQ4: 8 conditions × 7 models × 264 tasks × 3 seeds = 44,352 matrix runs, plus the
RQ1, RQ2 and RQ4 studies and the analysis.

Jobs run the repository's own harness (`evaluation/`) and engine (`src/`). Every location that
`evaluation/` writes to or reads from can be redirected with an environment variable. When a
variable is unset, the module uses the repository layout, so local runs are unchanged. The
variables are:

| Variable | Location | Default |
|---|---|---|
| `AM2M_RESULTS` | results | `results/` |
| `AM2M_DATA` | Who&When clone | `data/` |
| `AM2M_DOCS_OUT` | LaTeX tables and figures | `docs/` |
| `AM2M_WEB_OUT` | website data | `web/src/data/results.json` |
| `AM2M_REPORT` | report | `docs/results-qwen7b.md` |

The jobs set all five so that a run only writes into its own directory on scratch.

## Quick start

```bash
cd /project/6108124/majid/test/M2MColab
bash hpc/slurm/submit_pipeline.sh --dry-run      # show the job graph
bash hpc/slurm/submit_pipeline.sh                # submit the full study (prints RUN_ID and job IDs)
bash hpc/status.sh                               # colour audit of the latest run: jobs > steps > substeps,
                                                 # status, run time, failures with cause (saved as logs/<RUN_ID>/audit.txt)
bash hpc/status.sh --only failed                 # only failed / cancelled work
bash hpc/status.sh --watch 120                   # refresh every 120 s (minimum 60; one watcher at a time)
```

The login node only freezes the configuration and calls `sbatch`. Everything else runs in jobs,
including downloads, model pulls and the analysis.

## Job graph

| Stage | Script | Resources | What it runs | Research question |
|---|---|---|---|---|
| 00 | `00_setup_models_and_data.sh` | 1×H100, 8 h | Connectivity probe; installs Ollama 0.30.6 (bin + GPU runners) on scratch, plus the wheels missing from the wheelhouse (`pyecore`, `python-docx`). Clones Who&When and downloads ClassEval/HumanEval+ and the nltk data. **Checks:** every reference solution passes its hidden tests in the sandbox; all 7 models are pulled and answer at 100 % GPU; a **pre-flight** run (1 task per benchmark, Single + AutoM2M) completes. | gate for all |
| 10 | `10_rq3_condition_matrix.sh` ×14 | 1×H100, 24 h, auto-requeue | `run_matrix`: 8 conditions × all tasks × seeds 1–3, one job per model × benchmark | RQ3 (success, done, cost), RQ2 (admission) |
| 20 | `20_rq1_audit_rq2_checker_builder.sh` | 1×H100 | `rq2.mutate`, `rq2.scale`, `rq1.audit_whowhen`, builder ablation (v1 vs v2, 40 tasks), AutoM2M with one worked example | RQ1, RQ2 |
| 21 | `21_rq4_fault_injection_reference_team.sh` ×3 | 2×H100 | 40 injected faults/class on the reference team + 4 transcript baselines (Qwen-7B, Qwen-32B, Devstral) | RQ4 |
| 30 | `30_rq2_seeded_defects_vs_critics.sh` | 2×H100 | detach on natural teams, 300 seeded defects (DeepSeek-Coder-V2), clean set, checker vs 3 LLM critics | RQ2 |
| 31 | `31_rq4_fault_injection_builder_teams.sh` | 1×H100 | 200 injected faults on builder teams (Qwen-32B) + transcript baselines | RQ4 |
| 32 | `32_rq4_repair_vs_rebuild.sh` ×3 | 1×H100 | checked repair vs rebuild, 200 failed AutoM2M runs per model | RQ4 |
| 33 | `33_rq1_rq3_rq4_failure_coding.sh` | 2×H100 | LLM coders (Qwen-32B + Devstral): 150 natural failures, 184 Who&When + 400 Free/Critic failures, every failing executed run | RQ4, RQ1, RQ3 (H3a) |
| 40 | `40_analyze_and_collect_results.sh` | CPU | `analysis.analyze` (every table, figure and number), report, web JSON, per-model summaries, collection | all |

**Dependencies.**
- 10, 20 and 21 start after 00 succeeds (`afterok`). If setup fails, everything else is cancelled.
- 30–33 start after all matrix jobs end (`afterany`).
- 40 starts after everything ends.

**Restarts.** Jobs longer than their time limit receive `USR1` 15 minutes before the limit and
requeue themselves with `scontrol requeue`. The job ID stays the same, so dependencies hold.
Finished runs are skipped, because `run_matrix` resumes from its raw file. Finished steps are
skipped too, using `.done` markers in `$WORK/runs/<RUN_ID>/state/`. Runs that ended in `error`
are retried in up to `MATRIX_PASSES` passes.

## Configuration (`config/`)

- `pipeline.env` holds the account, paths, Ollama version, the subject models, and the auxiliary
  roles. As in the paper, coders are Qwen-32B + Devstral, the seeder is DeepSeek-Coder-V2, the
  judge is Qwen-32B, and the critics are Qwen-32B, Devstral and Qwen-7B. The paper leaves the
  critics as a TODO, so change them here if needed. The file also sets the RQ sizes (paper values).
- `models.tsv` sets `OLLAMA_NUM_PARALLEL`, workers and job time per model, sized to fit one 80 GB
  H100. If a step log shows `WARNING: not fully on GPU`, lower that model's `num_parallel`.
- `requirements.txt` lists the harness dependencies and the libraries that ClassEval's hidden tests
  import. They are installed per job into `$SLURM_TMPDIR` from the wheelhouse.

`submit_pipeline.sh` freezes `pipeline.env` and `models.tsv` into `logs/<RUN_ID>/`. Later edits
never change a running study.

## Where things go

| What | Where |
|---|---|
| Working results (scratch) | `$SCRATCH/autom2m_hpc/runs/<RUN_ID>/results/` |
| — one line per matrix run | `raw/<bench>__<model>.jsonl` |
| — full record per run (transcript or typed-team outcome, code, hidden-test score, every LLM call) | `runs/<bench>/<model>/<condition>/<task>__s<seed>.json` |
| — RQ studies | `rq1/ … rq4/` |
| — analysis | `data/*.csv`, `summary.json` (every number), `docs_out/{tables,figures}` (the paper's file names), `report.md`, `hpc_summary/` |
| Collected results (project, after stage 40) | `hpc/logs/<RUN_ID>/results/` (as above, with `runs/` as `runs.tar.gz`), next to the run's logs |
| Slurm stdout/stderr | `hpc/logs/<RUN_ID>/slurm/<job-name>_<jobid>.{out,err}` |
| Per-step logs (full output of every module) | `hpc/logs/<RUN_ID>/steps/<stage>/<step>.log` |
| Step ledger (job, restart, start, end, seconds, rc, command) | `hpc/logs/<RUN_ID>/steps.tsv` |
| Job endings, requeues | `hpc/logs/<RUN_ID>/jobs_finished.tsv`; job IDs in `jobs.tsv` |
| Ollama server logs, GPU samples (60 s), env + `pip freeze` | `hpc/logs/<RUN_ID>/{ollama,gpu,env}/` |
| Run manifest (git commit, code hashes, config) | `run_manifest.json` (in both places) |
| Model digests, quantisation, GPU share | `$WORK/runs/<RUN_ID>/setup/model_digests.json` |

`hpc_summary/` contains per-model tables that complement the paper's pooled analysis:

| File | Contents |
|---|---|
| `matrix_long.csv` | every run |
| `success_by_cell.csv`, `success_by_seed.csv` | success rates |
| `done_signal.csv` | precision, lift and recall of "done" |
| `cost.csv` | tokens and time |
| `admission.csv` | admission of typed teams |
| `status.csv` | run statuses |
| `completeness.csv` | progress against the full design |
| `rq_outputs.csv` | every RQ output file |
| `steps.csv` | pipeline steps |
| `summary.md` | all of the above as markdown |

## Common operations

```bash
bash hpc/status.sh <RUN_ID>                                   # status (run by hand, not in a loop)
bash hpc/slurm/submit_pipeline.sh --run-id <RUN_ID> --analysis-only        # intermediate analysis
bash hpc/slurm/submit_pipeline.sh --run-id <RUN_ID> --skip-setup --only coding,analysis   # resubmit stages
bash hpc/slurm/submit_pipeline.sh --models "qwen2.5-coder:7b" --only setup,matrix,analysis  # a subset
python hpc/scripts/run_pipeline.py matrix --model qwen2.5-coder:7b --bench classeval --list  # show a stage's steps
```

A resubmitted stage continues where it stopped. To redo a step from scratch, delete its `.done`
marker in `$WORK/runs/<RUN_ID>/state/` and the step's output file.

## Notes and known limitations

- **Internet.** The setup job needs internet access from a GPU node. If the probe fails, contact
  Alliance support, or stage the files into `$WORK`. That means the Ollama package, `wheels/`,
  `hf_cache/`, `data/`, and the models in `$SCRATCH/.ollama/models`.
- **PyPI wheels.** `pyecore` and `python-docx` come from PyPI because the wheelhouse lacks them. A
  support ticket can add them.
- **Sandbox.** LLM-written code runs in a resource-limited subprocess (`AGENTHOT_SANDBOX=process`).
  The network is not blocked; the paper lists this as a threat to validity.
- **Scratch.** Scratch is not backed up, and Nibi enforces a 1 TB soft limit after 60 days. Results
  are copied to `hpc/logs/<RUN_ID>/results/` by stage 40. Rerun `--analysis-only` to refresh that copy at any time.
- **Duration.** The full study takes days. The paper estimates about 3,200 workstation-hours, spread
  over up to about 20 concurrent H100 jobs depending on fairshare.
