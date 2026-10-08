# 06 — Baseline Comparison (free text vs. shared schema vs. agentm2m)

A small, self-contained, **pedagogical** (not statistically rigorous — that
is `evaluation/`'s job) side-by-side comparison of three hand-off
strategies on one deliberately tricky toy task: an Analyst's vague user
story handed to an Architect who must produce an API operation name and
signature, while a mandatory constraint ("cancellation must be blocked if
there is a pending refund") is supposed to survive the hand-off, and a
reference to the story's Epic is supposed to resolve to a real object.

Same underlying task (`STORY` in `run.py`), same LLM backend, three
different hand-off mechanisms:

```
run_freetext(llm)      -- a plain Python function, no structure, no validation
run_shared_schema(llm) -- one JSON-schema-validated shared dict (PatchBoard-style)
run_agentm2m(llm)      -- the real engine: metamodels.py + rules/ + Team/TeamRuntime
```

## What each path does

### 1. `run_freetext`

The "agent" is literally a Python function that serializes the story to
prose and asks the LLM backend for free text back:

```python
def _serialize_freetext(story: dict) -> str:
    # Deliberately lossy: DROPS the `constraint` field entirely.
    return f"User story {story['id']} (epic: {story['epic']}): {story['description']}"
```

The constraint is dropped **before the LLM ever sees the story** — this is
P1 (lossy hand-offs between heterogeneous views) in its purest form:
information silently lost at serialization time, with no mechanism to even
notice. There is also no validation at all of the LLM's raw output before
it is treated as the answer.

### 2. `run_shared_schema`

A single shared `dict` (`board`), written by asking the LLM for JSON with a
fixed set of required keys (`name`, `sig`, `component_ref`, `constraint`),
then checked against a hand-rolled schema validator (`_validate_against_schema`
in `run.py`) before being accepted into `board["operation"]`. `jsonschema`
is **not** installed in this project's `.venv` (checked before writing this
example), so the validator is a small hand-rolled shape-check instead of
adding a new dependency, per the brief.

This is strictly better than free text at one thing — **every write is
validated for shape** before acceptance — but it is not better at the
things a hand-off actually needs to preserve: `component_ref` is just a
free string the LLM wrote, never checked against anything that actually
exists, and `constraint` still has to pass **through the LLM's own
generation** rather than being copied — the schema guarantees a
non-empty string is *present* under that key, never that its *content*
matches the original. `docs/DS-A2A.tex`'s Discussion section puts this
precisely: *"A shared schema validates each write; agentm2m additionally
relates pairs of views, which is what change impact and coverage need."*
This example's table shows exactly that gap: `validator_enforced` is
`True` for shared-schema (the check ran), but `required_field_survived`
and `reference_resolved` are still `False`, because nothing here *relates*
the write to the rest of the model.

### 3. `run_agentm2m`

The real engine, `rules/Story2Arch.agentm2m`:

```
rule Story2Operation {
  from s : Story!UserStory
  to  op : Arch!Operation (
    component  <- s.epic,                 -- resolved via trace
    constraint <- s.constraint,           -- structural: never seen by the LLM
    signature  <- @llm('Derive an API operation signature for this user story',
                        s.description),   -- footprint EXCLUDES the constraint
    @check signature.parses() )
}
```

`constraint` is a **structural** binding — an ordinary OCL expression, not
an `@llm` call — so it is copied byte-for-byte from the source model and
is never at risk of being dropped, paraphrased, or hallucinated by an LLM
(Proposition 1: structural independence). `component` is resolved through
the hand-off's trace to the *actual* `Component` object generated from
`s.epic` by `Epic2Component`, not a string label the LLM invented. And
`signature`, the one genuinely LLM-authored field, is gated by
`@check signature.parses()` before it is ever accepted.

## The comparison table

`run.py` prints (with `--llm mock`; columns match the brief exactly):

```
Approach      | Required field survived? | Reference resolved? | Validator enforced? | Tokens sent to LLM
--------------+--------------------------+---------------------+---------------------+-------------------
free-text     | False                    | False               | False               | 43
shared-schema | False                    | False               | True                | 85
agentm2m      | True                     | True                | True                | 38
```

- **Required field survived?** Did the mandatory constraint text make it
  through to the final artifact? Only agentm2m guarantees this (a
  structural copy, never touched by the LLM); free text drops it before
  the prompt is even built, and shared-schema's freeform-generated
  `constraint` field cannot be trusted to match the original even though
  the schema requires the *key* to be present.
- **Reference resolved?** Does the produced artifact link to a real,
  existing upstream object (not just a label)? Only agentm2m's trace-based
  reference resolution (`component <- s.epic`) gives this; shared-schema's
  `component_ref` is an unverified string.
- **Validator enforced before acceptance?** Does *any* mechanism check the
  LLM's output before it is treated as accepted? Free text: no. Shared
  schema and agentm2m: yes — but "the write is well-formed" (shared schema)
  and "the write is well-formed *and* every structural/relational
  guarantee also holds" (agentm2m) are different claims.
- **Tokens sent to LLM** (`llm.count_tokens(prompt)`, `agentm2m.llm.base.
  LLMBackend.count_tokens`): agentm2m's footprint-bounded prompt (only
  `s.description`) is the smallest of the three, despite producing the
  *most* complete result — free text and shared schema both have to send
  more raw context per call precisely because they have no separate,
  cheap, deterministic channel (a structural binding) for the fields that
  don't need an LLM at all.

## Why this matters (P1, and Propositions 1–3)

`docs/DS-A2A.tex` names **P1: Lossy hand-offs between heterogeneous views**
as one of three coordination deficits behind MAST failure modes FM-1.4,
2.1, 2.4, 2.5 — information dropped or hallucinated silently because
hand-offs are free text re-interpreted by an LLM at every step. `run_freetext`
is a direct, minimal illustration of P1. `run_shared_schema` shows that
*validating shape* is not the same fix: PatchBoard-style shared state
(Sec IV/Discussion) makes outputs well-formed, but still routes every field
— including ones that should be exact copies or real references — through
the LLM, with no relation between views.

None of Propositions 1–3 hold for either baseline, and not by omission —
by construction:

- **Proposition 1 (structural independence)** requires that references be
  resolved through a persistent trace and that stochastic bindings never
  touch structure. Neither baseline has a trace; `component_ref` in
  shared-schema is exactly the kind of un-resolved string reference
  Proposition 1 rules out.
- **Proposition 2 (sound change impact)** requires footprint-bounded
  prompts and a version-stamped trace to compute `Obl(Delta)`. Neither
  baseline has a footprint concept (both serialize/re-serialize the whole
  artifact) or a trace to stamp, so there is nothing for an `Obl(Delta)` to
  be computed *over* — a downstream edit would have to be entirely
  re-run, or missed.
- **Proposition 3 (bounded residual error)** depends on a validator with
  real discriminating power (`d`) between correct and incorrect samples;
  free text's `d = 0` (no validator exists at all), so its residual error
  `epsilon = p` exactly.

Only `run_agentm2m` has all three preconditions in place, which is why it
is the only path whose table row is all `True`.

## Run it

```bash
python examples/06_baseline_comparison/run.py --llm mock       # deterministic, no network
python examples/06_baseline_comparison/run.py                  # local Ollama
```

Expected: the printed comparison table above (exact numbers may vary
slightly with a real model, but the free-text/shared-schema `False`s and
agentm2m's `True`s are structural, not model-dependent), and `phi holds: True`.

## Files

- `metamodels.py` / `seed_models.py` / `rules/Story2Arch.agentm2m` /
  `rules/helpers.py` — the tiny `Story -> Arch` engine path used only by
  `run_agentm2m`. `run_freetext` and `run_shared_schema` deliberately do
  **not** use these — they operate on plain Python dicts, since the whole
  point is to compare agentm2m's structural guarantees against two
  baselines that have no metamodel at all.
- `run.py` — the one shared `STORY` task, all three `run_*` functions, the
  comparison-table printer, and the CLI entry point.
