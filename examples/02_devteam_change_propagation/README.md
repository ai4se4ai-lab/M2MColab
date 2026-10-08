# 02 — Change propagation (Proposition 2)

Replays the paper's "Change scenario" runbox (Sec III-C) on top of
`examples/01_devteam`'s team, unchanged: run once, edit a source element,
run again, and inspect exactly what got re-sampled.

## What it demonstrates

- **Sound change impact (Proposition 2).** The Analyst tightens criterion
  `S2.1`. Only two bindings are re-invoked: `Story2Operation`'s `signature`
  for story `S2` (Req2Arch) and `Criterion2TestCase`'s `oracle` for `S2.1`
  itself (Req2Test) — story `S1`'s operation, `S1`'s test cases, and even
  `S2`'s own `CodeEdit.body` are left untouched.
- **No separate diffing pass.** The engine never computes a "diff" of the
  Req model. Algorithm 1's stamp check (`stamp(t,b) != #den(e_b)_m`) is
  run fresh on every call to `run_to_fixpoint()`; an obligation is exactly
  a stamp mismatch. `Obl(Delta)` in the paper is this mechanism's *report*,
  not a separate data structure the engine has to build.
- **Re-sampling vs. cascading are two different checks.** The Architect's
  `signature` binding *is* re-invoked (its footprint, `s.criteria`,
  changed) — but if the LLM happens to resample the same text, the
  Developer's `CodeEdit.body` (whose footprint is `op.signature`) is
  correctly left alone, because *its own* stamp still matches. The example
  prints both facts so the distinction is visible.

## Run it

```bash
python examples/02_devteam_change_propagation/run.py --llm mock
python examples/02_devteam_change_propagation/run.py    # local Ollama
```

Expected: the second run's obligations are a strict subset of the first
run's, and `All obligations touch only S2/S2.1-derived elements: True`.
