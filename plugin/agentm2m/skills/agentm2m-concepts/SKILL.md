---
name: agentm2m-concepts
description: Background on how an agentm2m team works (views, hand-offs as M2M transformations, stochastic bindings, footprints, trace links, obligations, escalation, phi, higher-order transformations). Use when working in a project with a .agentm2m/ directory, or when the user asks how agentm2m decides what to redo, why a value was rejected, or when the team is done.
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
