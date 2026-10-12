# 03 — Runtime team evolution (higher-order transformation)

Starts from `examples/01_devteam`'s four-agent team, runs it, then --
mid-process -- adds a fifth agent, **Security Reviewer**, related to
`Arch!Operation`. This is Fig. 1's dashed edge and Sec III-D's scenario:
*"add a Security Reviewer with view M_sec related to Operation."*

## What it demonstrates

- **A HOT is just data + `apply_hot`.** `agenthot.team.hot.TeamChange` is
  the declarative "relation model"; `apply_hot` registers the new agent,
  view, write right, and hand-off. No code generation, no hand-written
  glue between the new agent and the existing four.
- **Retroactive obligations (P3).** The new hand-off's trace starts empty,
  so its very first run treats *every* pre-existing `Operation` as an
  unresolved match — the Security Reviewer gets a review obligation for
  every operation that existed before it joined, automatically.
- **A strengthened acceptance predicate phi.** `runtime.acceptance_holds()`
  now also requires the new `Arch2Sec` hand-off's own phi to hold.

## Run it

```bash
python examples/03_security_reviewer_hot/run.py --llm mock
python examples/03_security_reviewer_hot/run.py    # local Ollama
```

Expected: before evolution, 4 agents; after, 5. The Security Reviewer's
view (`Sec.reviews`) ends up with exactly one entry per pre-existing
`Operation`, and `phi holds: True`.

## Files

- `metamodels.py` — `Sec` (`SecurityReview{notes, risk, operation}`),
  referencing `Arch!Operation` directly (a cross-metamodel `EReference`,
  see `MetamodelBuilder.reference`'s `target: str | EClass` parameter).
- `rules/Arch2Sec.agenthot` — the new hand-off, `T5` in Fig. 1.
- `run.py` — builds the original team (reusing `01_devteam/run.py`'s
  `build_team()` unchanged), runs it, then applies the HOT.
