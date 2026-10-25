# Pre-registration — Experiment 1: success as compute grows, by class size

Written 2026-10-22, before any run of this experiment. The analysis below is fixed; anything added later is reported
as exploratory.

## Hypotheses

- **H1 (budget scaling).** On ClassEval, AutoM2M's task success rises with its per-task budget more steeply than the
  token-matched Single-Gate baseline: a baseline that regenerates the whole class must get every method right in one
  sample, while AutoM2M validates and repairs each method separately.
- **H2 (class size).** The H1 slope difference is larger on large classes (>= 6 methods) than on small ones
  (<= 3 methods).
- **H3 (negative control).** On HumanEval+ (one function per task) there is no slope advantage for AutoM2M.

## Design

- Budget levels: AutoM2M with k = 1, 2, 4, 8 samples per binding (`AM2M_K`), repair rounds fixed at 2. Each level
  writes its own results directory (`results/k<K>`); Single-Gate at that level gets AutoM2M's median output tokens at
  that level as its budget (`gate_budget`, unchanged code), so the two are token-matched level by level.
- Fixed reference lines (not budget-scaled, as they end their own runs): Single, Free, Critic from the study run.
- Code: the v3 runtime (CHANGES.md, "Method revision v3") for every typed run; baselines unchanged.
- Models: Qwen2.5-Coder 7B and 32B.
- Pilot: development subset (`hpc/config/dev_tasks_*.txt`: 20 ClassEval, 33 HumanEval+ tasks), seed 1. Used only to
  find bugs and estimate cost; its numbers are not used for the claims.
- Confirmatory: the 80 held-out ClassEval tasks and the 131 held-out HumanEval+ tasks, seeds 1-3.

## Metric and analysis

- Success: all hidden tests pass. Budget: mean output tokens per run at each level (both conditions).
- Primary test (H1): GEE logistic regression, success ~ condition x log2(k), clustered by task, exchangeable
  correlation; H1 holds if the condition x log2(k) coefficient is > 0 with p < .05.
- H2: the same model with a three-way term condition x log2(k) x large (large = >= 6 methods; classes with 4-5 methods
  are excluded from H2); H2 holds if that term is > 0 with p < .05.
- Plateau: the success difference between k = 4 and k = 8 per condition, with a task-clustered bootstrap 95% CI
  (2,000 resamples); a condition "plateaus" if the CI includes 0.
- H3: the H1 model on HumanEval+; H3 holds if the interaction is not significantly > 0.
- All curves are reported, whatever they show.
