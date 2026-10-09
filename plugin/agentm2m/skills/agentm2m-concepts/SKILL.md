---
name: agentm2m-concepts
description: Background on how an agentm2m team works (views, M2M hand-offs, stochastic bindings, footprints, trace links, obligations, escalation, phi, evolution) and how AutoM2M builds and checks one (typed teams, W1-W6, repair). Use when working in a project with a .agentm2m/ directory, or when the user asks how agentm2m decides what to redo, why a value was rejected, or when the team is done.
user-invocable: false
---

# agentm2m in one page

- **Views.** Each agent owns one view model conforming to its own metamodel (`.agentm2m/team.yaml`).
  Only the owner may edit it (write rights), and elements created by hand-offs are engine-owned.
- **Hand-offs are M2M transformations** (`.agentm2m/rules/*.agentm2m`, ATL style). The engine
  deterministically matches source elements, creates target elements, resolves references, and records
  a trace link per match. Nothing structural is left to an LLM, so nothing can be silently dropped:
  every matching source element gets its target element by construction.
- **Stochastic bindings** (`f <- @llm(prompt, footprint)`) are the only LLM work: one attribute value,
  produced from the declared footprint alone, accepted only if its `@check` validator passes. In
  host mode Claude produces these values through `next_bindings` / `submit_binding`.
- **Footprint discipline.** When filling a binding, use only the prompt returned for it. Reading other
  files or conversation context would make change impact unsound, because the engine re-derives a
  value only when its footprint changes.
- **Stamps and obligations.** An accepted value is stamped with a digest of its footprint. A change
  makes exactly the bindings whose footprint it touches stale: those are the obligations. Re-derived
  values that change propagate hop by hop; `impact` previews this without any LLM call.
- **Escalation.** After k rejected attempts on one footprint the binding escalates and is not retried
  until its footprint changes. It needs a source change or a human decision.
- **phi (done).** The engine, not an agent, decides completion: every match covered, every value
  accepted for its current footprint, nothing pending or escalated (`acceptance`).
- **Evolution.** `team_evolve` adds an agent, its view, and a hand-off while the team runs (a
  higher-order transformation); the new agent gets obligations for all existing matches.

# AutoM2M: when the team is built for you

- **Typed team.** Instead of a person writing views and rules, a *builder* (Claude, in host mode)
  writes one JSON object: agents and their tools, one view per agent, hand-off rules with structural
  `bind` paths and `llm` values (prompt, footprint paths, validators from a fixed library), goals, the
  deliverable, and `done = [cover(G), valid, fresh, noObl]`. The task is lifted into a fixed `Goal` view
  (`Task`, one `Method` per method to implement). State lives in `.agentm2m/auto/`.
- **Admission (W1-W6), decided without any LLM or execution:** W1 well typed (paths, features,
  validators), W2 one writer per view and one producing rule per class, W3 mandatory features bound,
  W4 anchored coverage (every goal flows into a value checked by a *behaviour* validator, and every
  view lies on such a flow), W5 the engine decides done and hand-offs are acyclic, W6 owners have the
  tools their validators need. A rejected team comes back with exact diagnostics; fix only those.
- **Running.** The admitted team compiles to ordinary AgentM2M rules; the same footprint discipline,
  stamps, obligations and escalation apply. Behaviour validators *execute* the value (doctest examples,
  the team's tests); a value is accepted only if they pass. Values waiting on an upstream value are
  held back until it is accepted.
- **Done** is phi: every goal covered by a behaviour-validated descendant, every accepted value still
  valid on the final models, all stamps fresh, nothing escalated.
- **Repair.** `auto_attribute` traces each failed clause to one rule, value and agent (and, with an
  engine LLM, classifies it: sampling, footprint, upstream, specification, validator). A repair is a
  revised team submitted through `auto_submit_team`: it is checked again, then applied in place
  (accepted values kept), as a hot extension, or by rebuilding.
- **Hosted service.** With `AGENTM2M_URL` and `AGENTM2M_API_KEY` set, the plugin's tools run on a hosted
  agentm2m service (per-key projects; `AGENTM2M_PROJECT` picks one) instead of the local engine.
