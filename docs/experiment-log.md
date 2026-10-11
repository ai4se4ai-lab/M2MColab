# Experiment log: Qwen2.5-Coder 7B

What changed in the code while the Qwen2.5-Coder 7B study ran, and which runs were redone. Raw data:
`results/raw/<bench>__qwen2.5-coder_7b.jsonl` (one row per run) and `results/runs/<bench>/qwen2.5-coder_7b/<condition>/`.
A run counts as done if its row is in the raw file; removing the row makes the schedule redo it.
Backups of every raw file before a purge are in `results/backup/` or were taken by hand before the purge.

## Fixes and the runs they invalidated

| # | Change | Where | Runs redone |
|---|---|---|---|
| 1 | Test runner serves the solution under any module name the tests import; public examples run in one namespace in docstring order | `src/autom2m/pywork.py` | Typed conditions and Single-Gate, ClassEval seed 1 |
| 2 | `test_valid` no longer counts tests stopped by a pending method as vacuous | `src/autom2m/vlib.py` | Typed conditions, ClassEval seed 1 |
| 3 | A list given to a single-valued feature becomes its element (or the joined text) instead of crashing | `src/agenthot/engine/binding.py` | Typed-NC, ClassEval seed 1 |
| 4 | Rules that write code or tests read the task outline (builder example and prompt) | `src/autom2m/examples.py`, `prompts.py` | AutoM2M, Typed-NC, ClassEval seed 1 |
| 5 | The `compiles` validator dedents a method before compiling it | `src/autom2m/vlib.py` | included in 4 and 6 |
| 6 | The Typed-Ref team reads the class outline in every footprint | `teams/make_fixtures.py` (`classeval_reference`) | Typed-Ref, ClassEval seed 1 |
| 7 | A many-valued `method` reference holding one goal Method locates the value | `src/autom2m/vlib.py`, `src/agenthot/session.py` | 3 Typed-NC runs; AutoM2M in 8 |
| 8 | Post-run builder repair uses localized pointer fixes instead of a full revised team | `src/autom2m/loop.py` (`_delta_v2`), `builder.py` | AutoM2M, Typed-Ref, ClassEval seed 1 |
| 9 | The third worked example asks only for documented behaviour, typed and prose alike | `src/autom2m/examples.py` (`TEAM_INV`, `PROSE_INV`) | see below |

Fix 8 is described in the paper's "Checked repair" paragraph. Fix 9 is reported under External validity.

## Fix 9: the worked-example wording

The third worked example's analyst asked for "edge cases and error conditions". Builder teams copied that rule
almost word for word. With Qwen 7B, their tests then demanded exceptions the task never specifies (`ValueError not
raised` was the most common rejection), and the developers' code had to raise them. On ClassEval, AutoM2M with the
three examples (13.0%) did worse than the one-example configuration (18.6%), which never sees that example.

The example now asks for the edge cases the docstring and examples state, never errors they do not mention, and
its tests assert only stated behaviour. The typed and prose renderings use the same wording, so every builder sees
equivalent examples.

Rerun with `evaluation/scripts/resume_after_examples.sh` (seed 1, both benchmarks):

- Free, Critic, Schema, Typed-NC and AutoM2M: their builders see the examples.
- Single-Gate, after AutoM2M: its budget is AutoM2M's median output tokens, so it must follow the new runs.
- The builder-admission ablation (40 ClassEval tasks). The old data is in `results/backup/`.

Kept:

- Single and Typed-Ref: no builder is involved.
- AutoM2M with one example: it only sees the meter example.

Phases 3 to 6 (RQ4, RQ1, RQ3, seeds 2 and 3, analysis) run after the reruns, so they read only the new runs.

## Runs kept by decision

The 100 Single runs (ClassEval seed 1) predate fix 1. Single runs the public examples once to decide whether it
stops, so it used an earlier version of the example runner. We kept these runs rather than redo them; seeds 2
and 3 use the current runner.

## Results before fix 9 (seed 1, success %, for the record)

| | ClassEval | HumanEval+ |
|---|---|---|
| Single | 34.0 | 86.0 |
| Single-Gate | 38.0 | 87.2 |
| Free | 23.0 | 68.3 |
| Critic | 18.0 | 64.0 |
| Schema | 25.0 | 74.4 |
| Typed-NC | 6.9 | 37.8 |
| AutoM2M | 13.0 | 56.1 |
| Typed-Ref | 16.0 | 66.5 |
| AutoM2M, one example | 18.6 (86 of 100 tasks) | not run |

On HumanEval+, 42 of 164 AutoM2M sessions (26%) were not admitted. The builder made no progress over three
repair rounds, mostly because of unanchored goals, views without a writer and invented agent names.
