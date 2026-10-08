# 04 — Research Team (n:m / multi-source hand-off)

Three agents collaborate on a research summary: **Literature-Reviewer**
(owns `Literature`: `Paper`/`Claim`), **Experiment-Designer** (owns
`Experiments`: `ExperimentPlan`), **Report-Writer** (owns `Report`:
`ReportSection`).

```
Literature --Lit2Plan(1:1)--> Experiments
     |                              |
     +---ExpLit2Report(n:m, both)-->+--> Report
```

## What it demonstrates

This example is entirely new domain content (a research-assistant team, not
DevTeam), and it demonstrates the one mechanism 01–03 never exercise: an
**n:m (multi-source) hand-off**.

`docs/DS-A2A.tex`, Sec III-A, says:

> Multi-source hand-offs ($n{:}m$, e.g., the Developer reads both
> $M_{\mathit{req}}$ and $M_{\mathit{arch}}$) are declared as in ATL, with
> several `from` models.

`rules/ExpLit2Report.agentm2m` is exactly that: its `create` line declares
**two** source models,

```
create OUT : Report from IN1 : Experiments, IN2 : Literature;
```

and its one rule's `from` clause binds pattern variables from **both**
aliases in the same match, with a guard that *relates* them:

```
rule PlanAndClaim2Section {
  from p : Experiments!ExperimentPlan, c : Literature!Claim
           (p.topic = c.topic)
  to  s : Report!ReportSection ( ... )
}
```

**How this differs from 01_devteam's hand-offs.** Every hand-off in
`examples/01_devteam` (`Req2Arch`, `Arch2Code`, `Req2Test`) has exactly one
`from` model — even though the *team* has four views wired into a network,
each individual transformation only ever reads one upstream view at a time.
Here, `ExpLit2Report`'s single rule reads **two** upstream views
*simultaneously* inside one match: the guard `p.topic = c.topic` pairs an
`ExperimentPlan` with every `Claim` that shares its topic, and the
stochastic `body` binding's footprint,
`p.combinedFootprint(c)` (a helper returning `[p, c]`), is built from
**both** source elements at once — something a 1:1 hand-off's footprint,
bounded to a single source element or its sub-collection, cannot express.

This is also a *genuine* n:m match, not a disguised 1:1: the seed model
gives the topic `"latency"` to two claims from two different papers (`C2`,
`C4`). Since `Lit2Plan` (the ordinary 1:1 hand-off feeding `Experiments`)
derives one `ExperimentPlan` per `Claim`, that topic ends up with two plans
*and* two claims, so `ExpLit2Report` matches all four combinations
(`P(C2)×C2`, `P(C2)×C4`, `P(C4)×C2`, `P(C4)×C4`), producing four
`ReportSection`s from just two claims — real fan-out, visible in `run.py`'s
final `n_sections > n_plans` check.

| Mechanism | Where |
|---|---|
| n:m hand-off declaration | `rules/ExpLit2Report.agentm2m`'s `create OUT : Report from IN1 : Experiments, IN2 : Literature;` |
| A guard relating two source models | `(p.topic = c.topic)` |
| A footprint drawn from both sources | `p.combinedFootprint(c)` -> `[p, c]` (helpers.py) |
| Structural cross-view references | `ReportSection.plan` / `ReportSection.claim`, both set directly (no LLM involvement) |
| Stochastic binding with `@check` | `body <- @llm(...)`, `@check body.notTooShort()` |
| Real n:m fan-out (not disguised 1:1) | seed model's shared `"latency"` topic -> 2 plans x 2 claims = 4 sections |

## Run it

```bash
python examples/04_research_team/run.py --llm mock       # no network needed
python examples/04_research_team/run.py                  # local Ollama (default; needs `ollama serve`)
python examples/04_research_team/run.py --llm anthropic --model claude-haiku-4-5-20251001
```

Expected output: a per-hand-off report, the generated `Experiments` and
`Report` models, a fan-out check (`6` sections from `4` plans with the mock
backend), and `phi holds: True`.

## Files

- `metamodels.py` — `Literature` (`Paper`, `Claim`), `Experiments`
  (`ExperimentPlan`), `Report` (`ReportSection`, referencing *both*
  `ExperimentPlan` and `Claim`).
- `seed_models.py` — two papers, four claims, one topic shared across
  papers (the n:m trigger).
- `rules/Lit2Plan.agentm2m` — the ordinary 1:1 hand-off feeding
  `Experiments`.
- `rules/ExpLit2Report.agentm2m` — the n:m hand-off; see above.
- `rules/helpers.py` — `notTooShort` (a reused length `@check`), `sectionId`
  (disambiguates ids across an n:m match), `combinedFootprint` (the
  two-source footprint).
- `run.py` — wires the three views into a `Team`, runs to a fixpoint, and
  prints the fan-out check.
