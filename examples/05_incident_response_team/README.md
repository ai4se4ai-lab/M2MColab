# 05 — Incident Response Team (Lift + executable-oracle validator + escalation)

Four agents run an SRE pipeline: **Monitor** (owns `Alerts`), **Triage**
(owns `Incidents`), **Remediation** (owns `Actions`), **Postmortem** (owns
`Reports`).

```
Alerts --Alert2Incident--> Incidents --Incident2Action--> Actions --Action2Report--> Reports
```

## What it demonstrates

This example exercises two mechanisms none of 01–04 use.

### 1. Lift (text-to-model)

`rules/Incident2Action.agentm2m`'s `Incident2RemediationAction` rule has a
stochastic binding literally named `self`:

```
self <- @llm('Respond with a JSON object with exactly the fields name, command, '
             'and risk_level describing a recommended remediation action for this '
             'incident. risk_level must be one of low, medium, or high.',
             i.summary)
```

Per `agentm2m.engine.lift`'s convention (`LIFT_BINDING_NAME = "self"`), a
stochastic binding named `self` is not assigned to one attribute like an
ordinary binding — its sampled text is parsed as JSON by
`lift_json_into_element` and written onto **every** EAttribute the JSON
names (`name`, `command`, `risk_level` all at once), populating the whole
`RemediationAction` from a single LLM call. It is rejected — which the
engine treats exactly like a failed `@check`, triggering resample /
escalation — unless every JSON key names a real EAttribute of
`RemediationAction`; an LLM that hallucinates an extra field (or returns
non-JSON) never reaches the model at all.

### 2. An executable-oracle validator, and a genuine escalation

The same rule also has an ordinary stochastic binding, `dryRun`, gated by

```
@check dryRun.passesDryRun()
```

`passesDryRun` (`rules/helpers.py`) does not just parse the sampled text —
it **runs** it, as a real subprocess, via
`agentm2m.engine.validators.run_pytest_oracle` (write the sampled script to
a temp file, `python3` it, check the exit code). This is the "executable
oracle" case of Definition 1's `chk_b`: a parser only checks shape; this
validator checks that the remediation's dry-run script actually executes
cleanly before the engine will accept it.

Under `--llm mock`, the mock backend's deterministic fallback text is never
a runnable dry-run script, so `passesDryRun` rejects it on every attempt.
`run.py` also passes `TeamRuntime(..., max_resamples=1)`: since the mock
backend is fully deterministic, a second or third resample would produce
the exact same (still-rejected) text, so raising the budget would only cost
more subprocess calls for the same, foregone outcome. With budget
exhausted, `Incident2RemediationAction`'s `dryRun` binding **escalates** for
every `RemediationAction` — a real `Escalation` object, printed explicitly
by `run.py` under `=== Escalations (Proposition 3: 'escalate, never loop') ===`.

**What an escalation means operationally.** Per Algorithm 1 / Proposition 3
of `docs/DS-A2A.tex`, the engine never loops forever chasing an acceptable
sample and never silently commits an unvalidated value either: it samples
up to a budget `k`, and if nothing passes `chk_b`, it raises the escalation
and leaves that one binding unset (`ra.dryRun` stays `None`/default) while
everything else in the run proceeds normally (the `RemediationAction`
itself, its `name`/`command`/`risk_level` from the Lift binding, and the
downstream `PostmortemReport` are all still produced). An escalation is the
hand-off's explicit, structured signal that *this specific binding* needs a
human (or a different model, or a manual dry run) before it can be trusted
— never an infinite retry loop, and never a command that gets treated as
"safe to run" without ever having been validated.

Because of this, `runtime.acceptance_holds()` — phi for the whole team —
is `False` for this run: `Incident2Action`'s own phi requires no open
escalations, and this run has two. `run.py` prints `phi holds: False` and
explains why, exiting cleanly (code 0) rather than crashing — this is the
intended, deliberately engineered outcome of this example, not a bug in the
engine or the example.

| Mechanism | Where |
|---|---|
| Lift (`self <- @llm(...)`) | `rules/Incident2Action.agentm2m`, `self` binding |
| JSON->EAttributes conformance gate | `agentm2m.engine.lift.lift_json_into_element` |
| Executable-oracle `@check` | `rules/helpers.py`'s `passesDryRun`, reusing `agentm2m.engine.validators.run_pytest_oracle` |
| A genuine escalation (Proposition 3) | `dryRun` on every `RemediationAction`, always rejected under `--llm mock` |
| `phi holds: False`, explained, not crashed | `run.py`'s final block |

## Run it

```bash
python examples/05_incident_response_team/run.py --llm mock       # deterministically escalates; exit code 0
python examples/05_incident_response_team/run.py                  # local Ollama -- a real model may pass dryRun
```

Expected output: 2 incidents, 2 remediation actions (each with Lift-populated
`name`/`command`/`risk_level`), 2 `PostmortemReport`s, exactly 2
Escalations (one `dryRun` per `RemediationAction`), and `phi holds: False`
with an explanation. Exit code is always 0 for this example.

## Files

- `metamodels.py` — `Alerts` (`Alert`), `Incidents` (`Incident`), `Actions`
  (`RemediationAction`: `name`/`command`/`risk_level` lifted, `dryRun`
  executable-oracle-gated), `Reports` (`PostmortemReport`).
- `seed_models.py` — two raw alerts.
- `rules/Alert2Incident.agentm2m` — Monitor -> Triage, ordinary stochastic
  bindings with `@check`s.
- `rules/Incident2Action.agentm2m` — Triage -> Remediation: the Lift binding
  (`self`) and the executable-oracle-gated `dryRun` binding.
- `rules/Action2Report.agentm2m` — Remediation -> Postmortem, completing the
  four-agent pipeline.
- `rules/helpers.py` — `parsesRisk`, `notTooShort`, and `passesDryRun` (the
  executable-oracle validator).
- `run.py` — wires the four views into a `Team`, runs to a fixpoint with
  `max_resamples=1`, and prints the escalations and their meaning explicitly.
