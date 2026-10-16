# How the AutoM2M study runs on HPC (Narval / Nibi, Slurm)

This is the technical guide to `hpc/`. It covers how the study of `docs/emse-autom2m.tex` (the "Evaluation" section) is turned into Slurm jobs. It also covers how the source tree is used, how results are collected, how each phase is audited, and which Slurm techniques keep the run efficient and restartable.

`hpc/README.md` is the short operator's reference (commands, job table, file locations). This document explains why things are built the way they are and how the pieces connect. Where the two overlap, the scripts are the source of truth.

**Contents**

1. [The big picture](#1-the-big-picture)
2. [How the source tree is used on HPC](#2-how-the-source-tree-is-used-on-hpc)
3. [From the paper's experimental design to jobs](#3-from-the-papers-experimental-design-to-jobs)
4. [Coverage matrix: every claim of the paper → HPC step → output](#4-coverage-matrix-every-claim-of-the-paper--hpc-step--output)
5. [Anatomy of a job](#5-anatomy-of-a-job)
6. [Slurm techniques used](#6-slurm-techniques-used)
7. [Auditing every phase](#7-auditing-every-phase)
8. [Reproducibility and integrity controls](#8-reproducibility-and-integrity-controls)
9. [How results are collected](#9-how-results-are-collected)
10. [Operating the study](#10-operating-the-study)
11. [Limitations and open points](#11-limitations-and-open-points)

---

## 1. The big picture

The study has four research questions (RQ1–RQ4), eight experimental conditions, seven open-weight models, two benchmarks and three seeds. Together that is 8 × 7 × 264 × 3 = **44,352 matrix runs**, plus the RQ1, RQ2 and RQ4 side studies. The paper estimates about 3,200 workstation-hours for this. No single job can hold it, so `hpc/` splits it into a **graph of 26 Slurm jobs**. Each job is resumable, and every step it runs is recorded.

```
login node (no heavy work)             compute nodes (Narval A100 40 GB; or Nibi H100)
──────────────────────────             ────────────────────────────────────────────────────────────
submit_pipeline.sh                      00_setup ──afterok──┬─► 10_matrix ×14   (model × benchmark)
  ├ freeze config → logs/<RUN_ID>/      (1 GPU)             ├─► 20_rq2_builder
  ├ write run_manifest.json                                 └─► 21_rq4_ref ×3   (2 GPUs each)
  ├ sbatch the 26 jobs with                                        │
  │  --dependency                       all 10_matrix ──afterany──► 30_rq2_llm (2 GPUs)
  └ print first audit                                              ├─► 31_rq4_builder
                                                                   ├─► 32_rq4_repair ×3
status.sh / audit.py  (read-only)                                  └─► 33_coding (2 GPUs)
                                        everything ──afterany──► 40_analysis (CPU) ─► hpc/logs/<RUN_ID>/results/
```

Four ideas hold the design together:

1. **The harness is the repository's own code.** Jobs run `evaluation/` (harness, benchmarks, conditions, RQ studies, analysis) on top of `src/` (the AutoM2M and AgentHOT implementation). The HPC layer adds no experiment logic. It supplies placement, sizing, restarts, logging and auditing.
2. **Everything is idempotent and resumable.** Finished runs, finished steps and finished jobs are all skipped when something restarts. This lets 24-hour jobs requeue themselves until they are complete.
3. **Every action leaves a ledger entry.** Slurm accounting, per-job logs, per-step logs, a step ledger, GPU samples, environment dumps and a run manifest are all kept. Anyone can reconstruct afterwards what ran, where, for how long and with what result.
4. **The login node only submits.** Downloads, model pulls, benchmarks and analysis all run inside jobs, as the cluster policy requires.

---

## 2. How the source tree is used on HPC

### 2.1 Repository layout and who uses what

| Path | Role in the HPC study |
|---|---|
| `src/autom2m/` | The approach of the paper (see 2.2). Imported by the harness, never run directly by Slurm. |
| `src/agenthot/` | The execution layer AgentHOT ("hybrid hand-off transformations"): rule parser and compiler, engine (matcher, executor, trace, obligations, validators), sandbox, and the LLM backends. The Ollama backend and the metering wrapper are what the jobs use. |
| `teams/` | Typed-team fixtures: `classeval_reference.json` (Typed-Ref), the hand-written `devteam_admitted*.json` and `reqteam_admitted*.json` (mutation analysis), and `make_fixtures.py`. |
| `evaluation/` | The experiment harness. `hpc/scripts/run_pipeline.py` invokes it as `python -m evaluation.<module>`. |
| `hpc/` | The Slurm layer (this document). |
| `tests/`, `plugin/tests/` | LLM-free tests (`pytest tests plugin/tests`) for the harness, checker, engine and sandbox. They run in development or CI, not as pipeline steps (see §11). |
| `docs/emse-autom2m.tex` | The specification of what must be run. |
| `examples/`, `plugin/`, `web/` | Not used by the HPC study. |

### 2.2 `src/autom2m` ↔ the approach of the paper

| Paper component (Sec. 3) | File | Exercised by (HPC stage) |
|---|---|---|
| Lifter: task text → goal model M0 | `lift.py` | every `autom2m`, `typed_nc`, `typed_ref` run |
| Builder: propose / revise typed teams | `builder.py` (checked builder "v2"), `prompts.py`, `examples.py` (three worked examples) | stage 10 (`autom2m`, `typed_nc`), stage 20 (v1 vs v2 ablation; one-example run) |
| Typed team format Θ and its JSON Schema | `typed_team.py` | stages 10, 20, 30, 31 |
| Checker: well-formedness conditions W1–W6 | `checker.py` (W4 modes: `two-sided`, `one-sided`, `path-only`) | stage 00 (self-test), 10 (admission), 20 (mutation, scale), 30 (seeded defects) |
| Validator library (Table 4), tool requirements for W6 | `vlib.py` | all typed runs |
| Algorithm 1: build → admit → run → attribute → repair | `loop.py` (`AutoM2M`) | stages 10, 21, 31, 32 |
| Attribution: trace lookup + bounded replay | `attribution.py` | stages 21, 31 (RQ4) |
| Checked repair | `repair.py` | stage 32 (RQ4) |
| Python-methods domain and workbench | `pywork.py` | every benchmark run |
| CLI / MCP / server / plugin | `cli.py`, `mcp_server.py`, `server/`, `remote.py`, `workspace.py` | not used by the study |

`evaluation/` adds what is specific to the experiment:

| Area | Files | What it provides |
|---|---|---|
| Benchmarks | `benchmarks/tasks.py`, `benchmarks/sandbox.py` | ClassEval (100 tasks) and HumanEval+ (164 tasks) in one `Task` shape. `hidden_test` is read only by `score()`, never by a team, validator or builder. |
| Conditions | `conditions/baselines.py`, `conditions/common.py`, `run_matrix.py` | Single, Single-Gate, Free, Critic and Schema, plus the dispatch for Typed-NC, AutoM2M and Typed-Ref (which run on AgentHOT). |
| Harness | `harness/workbench.py`, `llms.py`, `coding.py` | `TaskWorkbench`, the LLM roles (coders, critics, seeder, judge, each as `model@seed`), and the codebook for failure coding. |
| RQ1 | `rq1/audit_whowhen.py`, `rq1/code_failures.py` | Specification audit and decisive-cause coding. |
| RQ2 | `rq2/mutate.py`, `natural_mutants.py`, `scale.py`, `independent.py`, `clean_teams.py`, `builder_ablation.py` | Mutation analysis, seeded defects, the clean set, critics, checker cost, and the builder ablation. |
| RQ3 | `rq3/code_runs.py` | Decisive cause of every failing executed run. |
| RQ4 | `rq4/inject.py`, `transcript.py`, `natural.py`, `repair.py` | Fault injection with ground truth, the four transcript baselines, natural failures, and repair vs rebuild. |
| Analysis | `analysis/export.py`, `analyze.py`, `stats.py`, `report.py`, `web_export.py` | Flattens raw logs to CSV, then produces every table, figure and number. |

### 2.3 How `evaluation/` is made location-independent

The harness must write only into a run's own scratch directory and never into the repository. For that, every output location is overridable through an environment variable. `common.sh::setup_python` sets all of them for each job:

| Variable | Set by the job to |
|---|---|
| `AM2M_RESULTS` | `$WORK/runs/<RUN_ID>/results` |
| `AM2M_DOCS_OUT` | `$AM2M_RESULTS/docs_out` (LaTeX tables, figures) |
| `AM2M_WEB_OUT` | `$AM2M_RESULTS/web/results.json` |
| `AM2M_REPORT` | `$AM2M_RESULTS/report.md` |
| `AM2M_DATA` | `$WORK/data` (Who&When clone) |
| `AM2M_OLLAMA` | the job's private Ollama URL |
| `AM2M_CODERS`, `AM2M_CRITICS`, `AM2M_SEEDER`, `AM2M_JUDGE` | the auxiliary-role models from `pipeline.env` |
| `AM2M_BUILDER` | `v2` (the checked builder) |
| `AM2M_SANDBOX_PY`, `AGENTHOT_SANDBOX=process` | the venv interpreter, used by the sandbox to run LLM-written code and hidden tests |
| `PYTHONPATH` | `<repo>:<repo>/src` |

When these variables are unset, the modules fall back to the repository layout, so local runs behave exactly as before.

---

## 3. From the paper's experimental design to jobs

### 3.1 The design in the paper ("Experimental design")

| Element | Paper | Where it is configured on HPC |
|---|---|---|
| Subjects | ClassEval (100 tasks), HumanEval+ (164), Who&When (184 annotated failures: 126 CaptainAgent + 58 Magentic-One) | `BENCHES`, `prefetch.py`, `AM2M_DATA` |
| Models | 7 instruction-tuned code models, 4-bit, via Ollama | `MODELS` in `pipeline.env`; sizing per model in `models.tsv` |
| Conditions | 8 (Single, Single-Gate, Free, Critic, Schema, Typed-NC, AutoM2M, Typed-Ref) | `CONDITIONS` |
| Seeds | 3, with task-level majority outcome | `SEEDS="1,2,3"` |
| Builder / agent / repair model | the same model within a configuration | `--model` of the matrix step |
| Auxiliary roles | coders Qwen2.5-Coder 32B + Devstral; seeder Gemma 3 27B; judge Qwen2.5-Coder 32B; 3 critics (two larger, one 7B) | `AM2M_CODERS/SEEDER/JUDGE/CRITICS` (the paper leaves the critics as a TODO; `pipeline.env` picks Qwen-32B, Devstral and Qwen-7B) |
| Hyper-parameters (temperature 0.2/0.6, 4,096 output cap, k = 3, k_prop = 3, k_rev = 2, k_adm = 3, k_rep = 2, n_rep = 3, h = 2) | fixed | inside `evaluation/` and `src/`; the frozen run manifest records the content hash of both trees |
| Sizes | 300 seeded defects (60 per class); 400 Free/Critic failures coded; 600 + 200 injected faults; 150 natural failures; 600 repair runs | `RQ2_PER_CLASS=60`, `RQ1_OURS_N=400`, `RQ4_PER_CLASS=40`, `RQ4_REF_MODELS` (3 × 200), `RQ4_BUILDER_MODEL`, `RQ4_NATURAL_N=150`, `RQ4_REPAIR_N=200` × 3 models |

The 44,352 runs divide into **14 matrix jobs** (7 models × 2 benchmarks). Each job runs all 8 conditions × all tasks × 3 seeds for its (model, benchmark) cell: 2,400 runs for ClassEval, 3,936 for HumanEval+. A job holds only one model, so its 1–2 A100s (`gpus` column of `models.tsv`) keep that model fully resident.

### 3.2 Stage-by-stage mapping

| Stage / job | GPUs | Steps (in order) | Research question |
|---|---|---|---|
| **00** `am2m_00_setup` | 1 | `prefetch_data` → `verify_data` → `pull_models` → `verify_models` → `selftest_checker` → `preflight_classeval` → `preflight_humanevalplus` → `preflight_check` | gate for all stages |
| **10** `am2m_10_matrix_<model>_<bench>` ×14 | 1 | `run_matrix_<bench>_pass{1,2,3}` each followed by `matrix_check_…` | RQ3 (success, done, cost); RQ2 (admission of builder teams) |
| **20** `am2m_20_rq2_builder` | 1 | `rq2_mutate` → `rq2_scale` → `rq1_audit_whowhen` → `rq2_builder_ablation` → `rq2_autom2m_1ex` | RQ2 (mutation, cost of checking, builder ablation, example copying); RQ1 (specification audit) |
| **21** `am2m_21_rq4_ref_<model>` ×3 | 2 | `rq4_inject_ref` → `rq4_transcript_ref` | RQ4 on the reference team (3 × 200 = 600 injected faults) |
| **30** `am2m_30_rq2_llm` | 2 | `rq2_natural_mutants` → `rq2_independent_seed` → `rq2_independent_clean` → `rq2_independent_critics` | RQ2 (detach on natural teams, 300 seeded defects, clean set, checker vs three critics) |
| **31** `am2m_31_rq4_builder_<model>` | 1 | `rq4_inject_builder` → `rq4_transcript_builder` | RQ4 on builder-generated teams (200 faults) |
| **32** `am2m_32_rq4_repair_<model>` ×3 | 1 | `rq4_repair` | RQ4 (checked repair vs rebuild; 3 × 200 failed AutoM2M runs) |
| **33** `am2m_33_coding` | 2 | `rq4_natural` → `rq1_code_whowhen` → `rq1_code_ours` → `rq3_code_runs` | RQ4 (150 natural failures), RQ1 (584 failures), RQ3 / H3a (every failing executed run) |
| **40** `am2m_40_analysis` | 0 (CPU) | `rq2_natural_mutants_refresh` → `analyze` → `report` → `web_export` → `summarize` → `collect` | all |

The count is 1 + 14 + 1 + 3 + 1 + 1 + 3 + 1 + 1 = **26 jobs** and 122 steps. The audit header shows the same totals.

### 3.3 Why the dependency graph looks like this

- **10, 20 and 21 start after 00** with `afterok`. Setup is the gate (data verified, models pulled, pre-flight passed). If it fails, `--kill-on-invalid-dep=yes` cancels everything that follows.
- **30–33 wait for the matrix** because they consume matrix output. 30 needs admitted builder teams. 31 needs builder teams for injection. 32 needs failed AutoM2M runs. 33 needs failing runs to code. They use `afterany`, not `afterok`, so one failed or timed-out matrix job does not block the rest of the study. They also carry `afterok` on setup, so a setup failure still cancels them.
- **40 waits for everything** with `afterany`. It always produces a report and shows what is missing. Its `completeness.csv` and per-RQ output listing make gaps visible, which is why it can run on partial data. It can also be resubmitted at any time (`--analysis-only`) for an intermediate analysis.

### 3.4 Design details that preserve the experiment's validity

- **Condition order inside a matrix job.** Single-Gate's token budget is AutoM2M's median output tokens for the same model and benchmark, so Single-Gate must run after AutoM2M. The default condition order does this (`CONDITIONS` ends with `typed_ref,single_gate`).
- **Hidden tests are kept out of reach.** `benchmarks/tasks.py` only exposes `prompt`, signatures, docstrings and public examples to teams. `score()` is the only reader of `hidden_test`.
- **The same model plays every role within a configuration**, as in the paper. Only the auxiliary roles (coders, critics, seeder, judge) differ. Their models are pinned in `pipeline.env`.
- **The seeder is not a critic.** The paper requires that the defect seeder belongs to a model family not used as a critic. `AM2M_SEEDER` is Gemma 3 27B (Google), and the critics are Qwen and Devstral.
- **Two coders from different families** (Qwen2.5-Coder 32B and Devstral) code failures. Disagreements go to an adjudication pass. This is configured through `AM2M_CODERS`, and `evaluation/coding.py` implements the tie-break rule.
- **An extra output file for the one-example configuration.** `rq2_autom2m_1ex` uses `--tag __1ex` so it never appends to the file that the 7B ClassEval matrix job writes at the same time. Concurrent appends from two nodes to one file would corrupt lines.
- **Statistics** (Cochran's Q, exact McNemar with Holm correction, GEE, Wilson and bootstrap intervals, Cliff's δ) are computed in `evaluation/analysis/` during stage 40 on the pooled data. This matches the paper's analysis plan, because the plan needs all seeds and models at once.

---

## 4. Coverage matrix: every claim of the paper → HPC step → output

This is the traceability table. For each claim or measurement in the Evaluation section, it names the step that produces the data and the artefact in the paper that depends on it.

| # | Paper claim / measurement | HPC step(s) | Raw output (under `$RESULTS`) | Paper artefact |
|---|---|---|---|---|
| **Setup and sanity** | | | | |
| S1 | Benchmarks are the right size, and the Who&When corpus is complete (126 + 58) | `verify_data` | `setup/verify_data.json` | Experimental design, "Subjects" |
| S2 | Hidden tests are a valid oracle (reference solutions pass) | `verify_data` (thresholds `CANONICAL_MIN_CLASSEVAL=0.95`, `…HUMANEVAL=0.98`) | `setup/verify_data.json` | Threats (oracle) |
| S3 | Seven models, 4-bit, run locally | `pull_models`, `verify_models` | `setup/model_digests.json` | Experimental design, "Models" |
| S4 | Checker and harness work end to end before 44k runs | `selftest_checker`, `preflight_*` | `preflight/` | – |
| **RQ1 (diagnosis)** | | | | |
| 1a | Specification audit of the 126 CaptainAgent teams (0/356 roles name a teammate, etc.) | `rq1_audit_whowhen` | `rq1/audit_whowhen.json`, `rq1/audit_matches.jsonl` | Table 1 (defects) |
| 1b | Decisive cause of the 184 Who&When failures | `rq1_code_whowhen` | `rq1/codes_whowhen.jsonl` | Fig. `fig_rq1_causes` |
| 1c | Decisive cause of 400 Free/Critic failures, stratified by model and benchmark | `rq1_code_ours` | `rq1/codes_ours.jsonl` | Fig. `fig_rq1_causes` |
| 1d | Decision rule: lower 95% bound > 20% | `analysis.analyze` (bootstrap CI, κ) | `summary.json` | Answer to RQ1 |
| **RQ2 (prevention)** | | | | |
| 2a | Mutation analysis: 88 mutants, 3 W4 variants × G1/G2 | `rq2_mutate` | `rq2/mutants.jsonl` | Table `tab_mutation` |
| 2b | "Detach" on natural builder teams | `rq2_natural_mutants` (+ refresh in stage 40) | `rq2/natural_mutants.json` | Table `tab_mutation` |
| 2c | 300 independently seeded defects (60 per class) | `rq2_independent_seed` | `rq2/seeded.jsonl` | `fig_independent` |
| 2d | Clean set → false-alarm rate | `rq2_independent_clean` | `rq2/clean.jsonl` | `fig_independent` |
| 2e | Checker vs three LLM critics on the same JSON spec | `rq2_independent_critics` | `rq2/independent.jsonl` | `fig_independent` |
| 2f | Admission of builder proposals by model | stage 10 (`autom2m`, `typed_nc` rows: `admitted`, `admitted_round`, `first_violation`) | `raw/*.jsonl` | `fig_admission` |
| 2g | Builder v1 vs v2 ablation | `rq2_builder_ablation` (40 tasks) | `rq2/builder_ablation__<model>.jsonl` | RQ2 results (builder ablation) |
| 2h | Example copying: one vs three worked examples | `rq2_autom2m_1ex` | `raw/classeval__<model>__1ex.jsonl` | Experimental design, "Procedure" |
| 2i | Cost of checking (ms, up to 3,000 views) | `rq2_scale` | `rq2/scale.json` | `fig_scale_a..d` |
| 2j | Decision rule: checker detection > best critic, false alarms < best critic | `analysis.analyze` | `summary.json` | Answer to RQ2 |
| **RQ3 (effect)** | | | | |
| 3a | Success per condition × model × benchmark × seed | stage 10 | `raw/<bench>__<model>.jsonl`, `runs/…/*.json` | `tab_success_classeval`, `tab_success_humaneval` |
| 3b | H3b non-inferiority (OR ≥ 0.80, pooled GEE) | `analysis.analyze` | `summary.json` | Answer to RQ3 |
| 3c | H3a composition-caused failures per 100 runs, under three codings of refused sessions | `rq3_code_runs` + `analyze` | `rq3/codes_runs.jsonl` | `tab_compfail` |
| 3d | H3c precision, lift and recall of "done" | stage 10 (`declared_done`, `success`) + `analyze` (+ `hpc_summary/done_signal.csv`) | `raw/*.jsonl` | `tab_donecost` |
| 3e | Cost: output tokens and wall-clock time | stage 10 (`total.out_tokens`, `seconds`) + `hpc_summary/cost.csv` | `raw/*.jsonl` | `tab_donecost`, `fig_scale_c/d` |
| 3f | Compute-matched Single-Gate | `single_gate` condition (budget = AutoM2M median) | `raw/*.jsonl` | Table of conditions (`tab:conditions`) |
| **RQ4 (attribution and repair)** | | | | |
| 4a | Fault injection, reference team, 3 models × 200 faults | `rq4_inject_ref` | `rq4/inject_ref__<model>.jsonl` | `fig_attribution` |
| 4b | Fault injection, builder teams, 200 faults | `rq4_inject_builder` | `rq4/inject_builder__<model>.jsonl` | `fig_attribution` |
| 4c | Four transcript baselines on the same runs (judge Qwen-32B) | `rq4_transcript_ref`, `rq4_transcript_builder` | `rq4/transcript_{ref,builder}__<model>.jsonl` | `fig_attribution` |
| 4d | 150 natural failures vs trace lookup | `rq4_natural` | `rq4/natural.jsonl` | RQ4 results |
| 4e | Checked repair vs rebuild (3 × 200 runs) | `rq4_repair` | `rq4/repair__<model>.jsonl` | RQ4 results |
| 4f | Decision rule: fault-class accuracy above the best baseline | `analysis.analyze` | `summary.json` | Answer to RQ4 |
| **Cross-cutting** | | | | |
| X1 | Every number, table and figure of the paper | `analyze`, `report`, `web_export` | `summary.json`, `docs_out/{tables,figures}`, `report.md` | all |
| X2 | Completeness against the 44,352-run design | `summarize` | `hpc_summary/completeness.csv`, `rq_outputs.csv` | – |

### Component → experiment (what exercises each part of the approach)

| Approach part | How the study tests it |
|---|---|
| Typed teams / hybrid hand-offs / AgentHOT runtime | Typed-NC, AutoM2M and Typed-Ref runs (stage 10); the typed-format-without-checker ablation (Typed-NC) |
| W1–W6 admission | Mutation analysis (2a, 2b), independent seeding (2c–2e), natural admission (2f), and the checker self-test in setup |
| Two-sided anchoring (W4) | The three W4 variants in 2a and 2c–2e |
| Builder (propose / revise / rank by checker) | v1 vs v2 ablation (2g), worked-example ablation (2h), admission per model (2f) |
| Completion φ (W5) | Precision, lift and recall of "done" (3d) |
| Capability matching (W6) | Mutation operators for D5, and seeded D5 defects |
| Attribution (lookup + bounded replay) | Fault injection (4a, 4b), natural failures (4d), and transcript baselines (4c) |
| Checked repair | Repair vs rebuild (4e) |
| Hand-written reference team (removes the builder) | Typed-Ref condition |
| Composition-defect taxonomy D1–D5 | Coding studies (1b, 1c, 3c) with the shared codebook in `evaluation/coding.py`; specification audit (1a) |

---

## 5. Anatomy of a job

Every job script is short. It sets `#SBATCH` options and then calls helpers from `hpc/slurm/common.sh`. For example, stage 10:

```bash
#SBATCH --time=24:00:00 --gpus=a100:1 --cpus-per-task=12 --mem=64G   # submit_pipeline.sh overrides --gpus/--cpus/--mem per stage
#SBATCH --requeue --open-mode=append --signal=B:USR1@900
source "$HPC_DIR/slurm/common.sh"
run_gpu_stage "$(model_field "$MODEL" 2)" 1 matrix --model "$MODEL" --bench "$BENCH"
```

`run_gpu_stage <num_parallel> <max_loaded> <stage args…>` does the following:

1. **`setup_traps`** installs an `EXIT` trap (writes the job ending to `jobs_finished.tsv`, stops Ollama and the GPU monitor) and a `USR1` trap (time-limit requeue, §6.3).
2. **`print_job_header`** logs job, node, CPUs, memory, GPUs, `RUN_ID`, git commit and paths, and lists the GPUs with `nvidia-smi`.
3. **`setup_python`** does `module purge && module load StdEnv/2023 python/3.11 arrow/24.0.0`. It then creates a `virtualenv --no-download` in `$SLURM_TMPDIR` and installs `hpc/config/requirements.txt` with `pip install --no-index --find-links $WHEELS`. It records `pip freeze` and an environment dump, and exports every `AM2M_*` path (§2.3).
4. **`start_ollama <par> <maxl>`** starts a **private Ollama server** on a free `127.0.0.1` port, pinned to the pre-installed version (default 0.30.6). `OLLAMA_NUM_PARALLEL` and `OLLAMA_MAX_LOADED_MODELS` come from `models.tsv` and `pipeline.env`. Readiness is checked with curl's own retry options (no `sleep`).
5. **`start_gpu_monitor`** samples `nvidia-smi` every 60 s into `logs/<RUN_ID>/gpu/`.
6. **`run_stage`** runs `python hpc/scripts/run_pipeline.py <stage> …` in the background and `wait`s on it, so the `USR1` trap can fire immediately.

### 5.1 The step runner (`hpc/scripts/run_pipeline.py`)

A stage is an ordered list of `Step` objects, either `python -m evaluation.<module>` commands or built-in actions (`pull_models`, `verify_models`, `matrix_check`, `preflight_check`). For each step the runner:

- checks that Ollama is healthy (LLM steps), and stops the stage if it is not (`rc = 3`, since nothing later can succeed);
- loads the step's models once at the step's `num_ctx` and **reports GPU residency**, with a visible `WARNING: not fully on GPU` if a model has spilled to CPU (the signal to lower `num_parallel`);
- writes the step's full stdout and stderr to `logs/<RUN_ID>/steps/<stage-key>/<step>.log`;
- appends a row to the **step ledger** `steps.tsv` (job, restart count, stage, step, start, end, seconds, return code, exact command);
- writes a `.done` marker on success, so a requeued job skips the step. Steps marked `always` (verification, checks, analysis) rerun every time;
- on failure, **logs it and continues** with the next step, since the steps of a stage are mostly independent. Setup is the exception. If `prefetch_data`, `verify_data` or `pull_models` fails, the stage stops. The stage's exit code is non-zero if any step failed.

`python hpc/scripts/run_pipeline.py <stage> [--model M] [--bench B] --list` prints a stage's steps without running anything. The audit uses this to build its plan (`plan.json`).

### 5.2 The matrix stage and its retry logic

The matrix stage runs `MATRIX_PASSES` (default 3) rounds of `run_matrix` for one (model, benchmark). Each round is followed by a `matrix_check` step. `run_matrix` appends one JSON line per finished run to `raw/<bench>__<model>.jsonl` and skips every `(task, condition, seed)` already present without error. A pass therefore only retries runs that ended in `error`. After each pass, `matrix_check` counts expected, done and error runs from the raw file. When all expected runs are done, the remaining passes are skipped. If the last pass still leaves gaps, the stage is marked failed with `matrix_incomplete`, which is visible in the audit.

---

## 6. Slurm techniques used

| Technique | Where | What it buys |
|---|---|---|
| **Job graph with dependencies** (`--dependency=afterok / afterany`, `--kill-on-invalid-dep=yes`) | `submit_pipeline.sh` | Expresses data dependencies in the scheduler instead of polling. A failed gate cancels dependents. Partial matrix failures do not block analysis. |
| **Right-sized GPU requests** (`--gpus=$GPU_TYPE:N` from `GPUS_*` in `pipeline.env` and the `gpus` column of `models.tsv`, with `CPUS_PER_GPU` cores and `MEM_PER_GPU_GB` each; CPU-only for analysis) | `slurm/*.sh` | No GPUs are reserved for work that does not need them. Analysis (stage 40) asks only for 4 CPUs and 32 GB. |
| **Self-requeueing long jobs** (`--requeue`, `--signal=B:USR1@900`, `scontrol requeue`) | `common.sh::_on_timelimit` | 24 h jobs finish multi-day workloads. Slurm sends `USR1` 15 minutes before the limit. The job stops its children, logs it and requeues itself under the same job ID, so dependencies stay valid. |
| **Requeue cap** (`MAX_REQUEUES=30`, restart count read via `scontrol show job … Restarts=`) | `common.sh` | `SLURM_RESTART_COUNT` was found not to be reliably set after requeue on this cluster. Reading the real count keeps the cap effective, so a broken job cannot loop forever. |
| **Append-mode logs** (`--open-mode=append`) | stage scripts | A requeue does not overwrite the previous attempt's Slurm output. |
| **Resumable, idempotent work** (raw JSONL skip-set, `.done` markers) | `run_matrix.py`, `run_pipeline.py` | A restart repeats nothing that finished. |
| **One job per (model, benchmark)** | `10_rq3_condition_matrix.sh` ×14 | The 44,352 runs spread over up to 14 concurrent jobs (20 A100s on Narval). A job holds one model, so the weights stay resident. |
| **Bundled tasks, long jobs** | stage design | No tiny jobs. Every job runs for hours, which respects the "bundle tasks under 15 minutes" guidance. Each stage runs several steps inside one allocation. |
| **GPU memory sizing per model** (`models.tsv`, `AUX_PAR_*`, `90_check_gpu_sizing.sh`) | config, sizing job | `OLLAMA_NUM_PARALLEL` (KV-cache slots) and worker counts are matched to the stage's A100s (40 GB each) so that every model is 100% on GPU. The run logs `WARNING` if one is not. The sizing job (not part of the graph) tests every stage's model set, context size and parallelism before a big submission. It showed, for example, that 6 slots made models evict each other in multi-model stages, so those use 2. |
| **Workers = Ollama slots** | `models.tsv` | `run_matrix --workers N` matches `OLLAMA_NUM_PARALLEL`. The GPU serves N requests at once and no request queue builds up. |
| **Private Ollama per job, free port** | `common.sh::start_ollama` | Jobs sharing a node cannot collide. Nothing is exposed beyond `127.0.0.1`. |
| **Node-local scratch for the environment** (`$SLURM_TMPDIR/venv`) | `setup_python` | Fast I/O. Nothing small-file-heavy lands on the shared filesystem. |
| **Wheelhouse, no PyPI by default** (`--no-download`, `pip install --no-index`) | `setup_python` | Alliance policy. Only `pyecore` and `python-docx` (missing from the wheelhouse) are fetched once into `$WHEELS` during setup. |
| **Environment modules** (`module purge; module load StdEnv/2023 python/3.11 arrow/24.0.0`) | `setup_python` | A defined, logged software stack (the module list is saved per job). |
| **Internet-dependent work isolated to one job** (probe of GitHub, Ollama registry, Hugging Face, PyPI; downloads; model pulls) | `00_setup_models_and_data.sh` | All other jobs run offline (`HF_*_OFFLINE=1`). If the probe fails (Narval: compute nodes have no internet by policy), the job checks the files staged by `hpc/slurm/stage_offline.sh` on a login node, skips all downloads (`SETUP_OFFLINE=1`) and only verifies them. |
| **Persistent cache on scratch** (Ollama binary, models, wheels, datasets under `$WORK`, `$OLLAMA_MODELS`) | `pipeline.env` | Installed once, reused by every job and run. |
| **No `sleep` in jobs, no tight polling** | `common.sh`, `audit.py` | Readiness uses `curl --retry`. The monitoring tools make one `squeue` and one `sacct` call per snapshot, with `--watch` at least 60 s. Only one watcher is meant to run. |
| **Login node does only light work** | `submit_pipeline.sh`, `status.sh` | The login node writes configuration, calls `sbatch`, and reads small files. |
| **Many small files → one archive** | `collect_results.sh` | Per-run JSON files (tens of thousands) are collected as `runs.tar.gz`, following Alliance guidance. |
| **Mail on failure** (`--mail-type=FAIL`, plus `END` for setup and analysis) | stage scripts | The researcher is told when attention is needed. |
| **Smoke test** (`submit_pipeline.sh --smoke`) | `submit_pipeline.sh` | Every stage runs end to end with 2 tasks, 1 seed, a handful of items per RQ, the single cached 7B model in all roles, and 1 h limits. This validates the full graph, including the dependencies, before the multi-day submission. |
| **Subset runs** (`--only`, `--models`, `--skip-setup`, `--analysis-only`, `--run-id`) | `submit_pipeline.sh` | Rerun just what is missing or broken, in the same run directory. |
| **Resource accounting for later tuning** | `sacct` (read by audit), GPU CSVs | Elapsed time against the time limit, node, exit code and GPU utilisation inform the next sizing. |

### 6.1 Why `afterany` for the downstream stages

The researcher does not need to watch the matrix. The analysis stage produces a complete picture of whatever finished, and the audit and `completeness.csv` list what is missing. A resubmission with `--only` then fills the gaps, and a rerun of `--analysis-only` refreshes the collected results.

### 6.2 Efficient use of the GPU budget

- **Model-per-job placement** avoids repeated loading of 7–32B weights.
- **KV-cache budgeting.** `models.tsv` documents why the slot counts differ. CodeLlama 13B has no grouped-query attention, so its KV cache is about 14× larger per slot and it gets only 4 slots.
- **Stage-specific context sizes.** Matrix and repair steps use `num_ctx = 16384`. Judge, coder and critic steps use 32768. The runner reloads a model when the context changes and reports residency each time.
- **GPU count follows residency.** Only stages that must hold two large models at once (the 32B judge with a subject model, the two coders, the critic set) request 2 GPUs.

### 6.3 The time-limit protocol, step by step

1. 15 minutes before the limit, Slurm signals `USR1` to the batch shell (`B:` means the signal goes to the batch script, not to every step).
2. `_on_timelimit` kills the running stage, the GPU monitor and Ollama, appends a `requeued` row to `jobs_finished.tsv`, and calls `scontrol requeue $SLURM_JOB_ID`.
3. The job restarts under the same ID. The frozen config is re-sourced, a new venv and Ollama are started, and the stage skips finished steps and finished runs.
4. After `MAX_REQUEUES` restarts the job exits with code 99 and says so in its log, which the audit shows as a failure.

---

## 7. Auditing every phase

Auditing happens at six levels. Each has a concrete file or command.

| Level | What it records / checks | Where |
|---|---|---|
| **1. Before the run** | Frozen configuration (`run_config.env`, `models.tsv`), git commit, branch, dirty paths, diff hash, content hashes of `src/`, `evaluation/` and `teams/`, submission times | `logs/<RUN_ID>/run_manifest.json` (copy in `$WORK/runs/<RUN_ID>/`), written by `manifest.py` |
| **2. Setup gates** | Data completeness, reference solutions pass hidden tests (≥ 95% ClassEval, ≥ 98% HumanEval+), third-party imports available to the sandbox, models present and answering at 100% GPU, checker self-test, pre-flight (1 task, Single + AutoM2M on both benchmarks) | `setup/verify_data.json`, `setup/model_digests.json`, `steps/setup/*.log` |
| **3. Job level** | State, node, run time against limit, queue time and reason, exit code, restarts. Job endings (`ok`, `FAILED(rc)`, `requeued`) | Slurm (`squeue`/`sacct`), `jobs.tsv`, `jobs_finished.tsv`, `slurm/<name>_<id>.{out,err}` |
| **4. Step level** | Start, end, seconds, return code and exact command of every step attempt, including requeues | `steps.tsv`, `steps/<key>/<step>.log`, `state/<key>/*.done` |
| **5. Substep / data level** | Per model and condition: runs done/expected, errors, success. Per-step model residency. Raw record per run (transcript or typed-team outcome, code, hidden-test score, every LLM call) | `raw/*.jsonl`, `runs/…/*.json`, `progress.py`, `audit.py` substeps |
| **6. After the run** | Completeness against the design, RQ output presence and sizes, failed steps, per-model tables | `hpc_summary/{completeness,rq_outputs,steps,status}.csv`, `summary.md` |

### 7.1 The live audit: `hpc/status.sh` / `hpc/scripts/audit.py`

```bash
bash hpc/status.sh                       # newest run: colour table jobs > steps > substeps, then data progress
bash hpc/status.sh <RUN_ID> --tree       # full tree
bash hpc/status.sh <RUN_ID> --only failed
bash hpc/status.sh <RUN_ID> --watch 120  # refresh every 120 s (minimum 60)
```

- **Hierarchy.** job (node) → step → substep, with status badges: OK, RUNNING, PENDING, FAILED, DONE EARLIER, INTERRUPTED, PARTIAL, CANCELLED, NOT RUN, TIMEOUT.
- **Sources.** One `squeue` and one `sacct` call per snapshot. Plus `jobs.tsv`, `steps.tsv`, the `.running` markers (live timer of the executing step), failure excerpts from step logs, per-condition progress read from `raw/*.jsonl`, and the cached `plan.json` (steps each job will run).
- **Substeps.** For matrix steps, per-condition progress (done/expected). For multi-model steps, one substep per model (loaded, resident, % GPU).
- **Failures show their cause.** The audit prints an excerpt from the failing step log next to the failed row.
- **Persistence.** Each snapshot is saved without colour as `logs/<RUN_ID>/audit.txt` and `audit.json`, so the audit itself is part of the record.
- **Cheap by design.** It is read-only and safe on a login node. `--watch` ends by itself when no job is pending or running.

Below the audit table, `status.sh` calls `progress.py`. It reads the raw files and prints matrix runs done/expected per model and benchmark (and per condition), errors, success so far, RQ output files with sizes, failed steps, and job-ending counts. It also warns about unparseable lines in `raw/*.jsonl`, which would indicate a concurrent write.

### 7.2 Audit of each phase of the project

| Phase | How it is audited |
|---|---|
| Environment (software, modules, wheels) | `env/env_<job>.txt`, `env/pip_freeze_<job>.txt`, module list per job |
| Data (benchmarks, Who&When) | `verify_data.json`: counts, third-party imports, canonical-solution pass rates, with failing task IDs listed |
| Models | `model_digests.json`: digest, quantisation, parameter size, family, GPU share. Residency lines in each step log |
| Inference server | `ollama/ollama_<job>_r<restart>.log`, `env/ollama_version_<job>.json` |
| GPU use | `gpu/gpu_<job>_r<restart>.csv` (utilisation, memory, power, temperature, every 60 s) |
| Harness correctness before the main run | `selftest_checker` (the mutation module runs on LLM-free data), `preflight_*` (real runs), `preflight_check` (fails if either condition errors or is missing) |
| Matrix | `matrix_check_*` per pass; `progress.py` per cell; `completeness.csv`; status counts per condition |
| RQ studies | One file per study under `rq1/ … rq4/`. `rq_outputs.csv` flags missing or empty files |
| Analysis | `analyze`, `report`, `web_export` return codes in `steps.tsv`; `summary.json` holds every reported number |
| Collection | `collect` step output (`collected: <size>`); a rerun is safe |

---

## 8. Reproducibility and integrity controls

- **Frozen configuration.** `submit_pipeline.sh` copies `pipeline.env` and `models.tsv` into the run's log directory the first time a run ID is used. Jobs source the frozen copy, so editing `config/` later never changes a running study. The experimenter's intent (models, sizes, auxiliary roles) is preserved with the results.
- **Code identity.** The manifest stores the commit, the branch, any dirty paths under `src/`, `teams/` and `evaluation/`, a hash of the working-tree diff, and content hashes of the three code trees. Two result sets can be compared by hash.
- **Seeds and determinism.** Three seeds per cell. Auxiliary roles carry explicit seeds (`model@seed`). LLM output is inherently non-deterministic, which the paper addresses with the three seeds and the task-level majority outcome.
- **Resumability does not change results.** A run is recorded once, and a skipped run is never recomputed. Retries only apply to runs recorded as `error`, and the last record for a key wins.
- **Isolation of outputs.** All output paths are redirected to the run's directory. Nothing in the repository (`docs/`, `web/`, `results/`) is overwritten by a job.
- **Sandboxing of generated code.** LLM-written code and hidden tests run in a resource-limited subprocess (`AGENTHOT_SANDBOX=process`) under the venv interpreter. The network is not blocked, and the paper lists this as a threat to validity.
- **Concurrent-write protection.** Separate raw files per (benchmark, model) and a `--tag` for the one-example configuration. `progress.py` and the audit detect corrupted lines.
- **Backups.** Scratch is not backed up. Stage 40 copies results to `hpc/logs/<RUN_ID>/results/` on `/project`, which is backed up.

---

## 9. How results are collected

### 9.1 During the run (scratch)

```
$SCRATCH/autom2m_hpc/runs/<RUN_ID>/
├── results/                         ($AM2M_RESULTS)
│   ├── raw/<bench>__<model>.jsonl     one summary line per matrix run
│   ├── runs/<bench>/<model>/<condition>/<task>__s<seed>.json   full record per run
│   ├── rq1/ rq2/ rq3/ rq4/            study outputs (jsonl)
│   ├── data/*.csv                     flattened by analysis.export
│   ├── summary.json                   every number reported in the paper
│   ├── docs_out/{tables,figures}/     the paper's file names (tab_*.tex, fig_*.pdf)
│   ├── report.md  web/results.json
│   └── hpc_summary/                   per-model tables (see below)
├── state/<stage-key>/*.done         step markers
├── setup/                           verify_data.json, model_digests.json
├── preflight/                       pre-flight results
└── run_manifest.json
```

A **raw line** for a matrix run contains the identifiers (`task`, `condition`, `seed`, `model`, `bench`), `status`, `success`, `test_pass_rate`, `declared_done`, token and time totals, and, for typed conditions, admission fields (`admitted`, `admitted_round`, `first_violation`).

### 9.2 Analysis (stage 40, CPU)

`analysis.export` flattens raw logs into `data/*.csv` (`runs.csv`, `rq1_codes.csv`, `rq2_mutants.csv`, `rq2_independent.csv`, `rq2_scale.csv`, `rq3_codes.csv`, `rq4_inject.csv`, `rq4_transcript.csv`, `rq4_natural.csv`, `rq4_repair.csv`). `analysis.analyze` then produces the paper's tables, figures and `summary.json`. `analysis.report` writes the markdown report, and `analysis.web_export` condenses it for the website, where missing studies render as "pending".

`hpc/scripts/summarize.py` adds per-model tables that complement the paper's pooled analysis, in `hpc_summary/`:

| File | Content |
|---|---|
| `matrix_long.csv` | every run |
| `success_by_cell.csv`, `success_by_seed.csv` | success (and task-majority success) per model × benchmark × condition (× seed) |
| `done_signal.csv` | precision, base rate, lift, recall and F1 of each condition's "done" (H3c) |
| `cost.csv` | median and mean seconds and output tokens, budget-hit share |
| `admission.csv` | admission rate and mean round for typed conditions; first violated condition |
| `status.csv` | run status counts |
| `completeness.csv` | done vs expected per model × benchmark × condition × seed |
| `rq_outputs.csv` | presence, row count and size of every RQ output file |
| `steps.csv` | latest attempt of every pipeline step |
| `model_digests.json`, `summary.md` | model identity; everything above as markdown |

### 9.3 Collection (`collect_results.sh`)

`rsync` copies the results directory to `hpc/logs/<RUN_ID>/results/`, excluding `runs/`. The tens of thousands of per-run JSON files become one `runs.tar.gz` (written to a `.tmp` name and renamed, so a half-written archive never appears). The paper's `tables/` and `figures/` are copied to the top level, together with the manifest. The step is safe to rerun, and `--analysis-only` refreshes it at any time.

### 9.4 Applying results to the paper

`docs_out/tables/*.tex` and `docs_out/figures/*.pdf` use the paper's file names. Copy them into `docs/` to rebuild the manuscript. The manuscript currently carries a "status of the data" notice because parts of its numbers come from synthetic data (see §11), so this step is deliberate and manual.

---

## 10. Operating the study

```bash
cd /project/6108124/majid/test/M2MColab

# 1. Check the graph without submitting
bash hpc/slurm/submit_pipeline.sh --dry-run

# 2. Optional: validate the whole graph cheaply (2 tasks, 1 seed, 7B only, 1 h limits)
bash hpc/slurm/submit_pipeline.sh --smoke

# 3. Optional: verify GPU sizing for every stage (1 GPU, 1.5 h, not part of the graph)
sbatch --account=<acc> --export=ALL,HPC_DIR=$PWD/hpc,RUN_ID=am2m-sizing hpc/slurm/90_check_gpu_sizing.sh

# 4. Full study
bash hpc/slurm/submit_pipeline.sh                      # prints RUN_ID and job IDs

# 5. Monitor (by hand; no loops)
bash hpc/status.sh                                     # or --only failed, --tree, --watch 120

# 6. Repair and resume
bash hpc/slurm/submit_pipeline.sh --run-id <RUN_ID> --skip-setup --only matrix,coding
bash hpc/slurm/submit_pipeline.sh --run-id <RUN_ID> --analysis-only
```

To redo a single step from scratch, delete its `.done` marker in `$WORK/runs/<RUN_ID>/state/<key>/` and its output file. To run one module by hand inside an interactive allocation (`salloc`), use `hpc/scripts/run_step.py <RUN_ID> <module> [args]`. It resolves the run's frozen paths, and outputs stay in the run's results directory.

**Guidance for operators**

- Heavy work always belongs in a job. If a run needs interactive debugging, use `salloc` with a minimal request.
- Keep tests small (`--smoke`, `--only`, `--models`). Do not resubmit the full study to test a small change.
- Check `diskusage_report` before the study. A full run writes a large number of files to scratch.
- If anything about internet access, quota, or wheel availability is unclear, contact Alliance support rather than working around it.

---

## 11. Limitations and open points

These are stated plainly so that nobody over-reads what the pipeline proves.

1. **Paper data status.** `docs/emse-autom2m.tex` contains a `\SyntheticNotice` and still lists `analysis/simulate.py` in the data-availability text with a TODO to remove it once real runs replace synthetic data. The HPC study is what produces the real data. Until its results replace the manuscript's tables, the paper's numbers are not those of this pipeline.
2. **Unit tests are not pipeline steps.** `pytest tests plugin/tests` (LLM-free tests of the checker, engine, harness, sandbox and the analysis on synthetic logs) is not run by the Slurm graph. The in-pipeline equivalents are the checker self-test, the pre-flight runs and `--smoke`. If a gate on the test suite is wanted, add it as a CPU-only `always` step in the setup stage.
3. **"Every aspect is tested" means every research question and its decision rule has a producing step** (§4). It does not mean every code path is covered. Aspects the paper itself marks as untested, such as footprint-only prompting against full context and incremental against from-scratch execution, have no step either.
4. **Subject-model change.** DeepSeek-Coder-V2 16B, the paper's original mixture-of-experts model and RQ2 seeder, was replaced by Gemma 3 27B: under Ollama its generation stalled (15-minute timeouts after one output token, on Nibi and Narval). The manuscript was updated; its result tables carry `TODO(author)` placeholders for the Gemma rows until this run's tables replace them.
5. **Sandbox is not network-isolated.** LLM-generated code runs in a resource-limited subprocess with network access.
6. **Critics are a configuration choice.** The paper leaves the three critic models as a TODO. `pipeline.env` fixes them (Qwen2.5-Coder 32B, Devstral, Qwen2.5-Coder 7B). Change them there before the run if the paper settles on others.
7. **Downloads on Narval run on the login node.** Narval compute nodes have no internet, so `hpc/slurm/stage_offline.sh` (download-only) stages the Ollama package, `wheels/`, `hf_cache/`, `data/` and `$SCRATCH/.ollama/models` before the setup job.
8. **Scratch retention.** Scratch is not backed up and old files are purged. Treat `hpc/logs/<RUN_ID>/results/` as the persistent copy and re-collect before any purge.
9. **Heavy but unavoidable cost.** The paper estimates about 3,200 workstation-hours. Wall-clock time on the cluster depends on fairshare and on how many of the 14 matrix jobs run at the same time.
10. **Per-step failure policy.** A failed step does not stop its stage (only setup gates and an unhealthy Ollama do). A stage can therefore end non-zero with most of its data present. Always read the audit and `completeness.csv` before treating a run as complete.
11. **`--smoke` is a plumbing test**, not a scientific result. It uses 2 tasks, 1 seed and the 7B model in every role, so none of its numbers should be reported.
