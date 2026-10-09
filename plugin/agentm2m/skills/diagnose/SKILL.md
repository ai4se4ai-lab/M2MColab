---
name: diagnose
description: Explain why an AutoM2M run is not done - locate each failed phi clause to a hand-off, rule, value and owning agent, classify the fault where possible, and propose a checked repair of the team. Use when auto_status reports phi false with nothing pending.
---

# Diagnose and repair a failed AutoM2M run

1. Call `auto_status`. Note `phi`, `open_clauses` and the failures (clause, rule, binding, agent, reason).
   If bindings are still pending, finish them first (`auto-build` step 4).
2. Call `auto_attribute`. Each fault names where it is (hand-off / rule.binding / agent) and, with an
   engine LLM, its class:
   - `sampling`: the value was just unlucky; another try on the same footprint passes.
   - `footprint`: the hand-off did not carry enough context; widen that binding's `footprint`.
   - `upstream`: an upstream LLM value is wrong; re-fill it.
   - `specification` / `coverage` / `validator`: the prompt, validator or team shape must change.
   In host mode faults are located but `unclassified`: decide from the `reason`.
3. Propose a repair: call `auto_propose` with `mode` = `delta`, answer with the revised typed team (change
   as little as possible: accepted values outside the changed parts are kept), and submit it with
   `auto_submit_team`. It is checked against W1-W6 before it is applied; report its `mode`
   (in-place / hot / rebuild) and `kept_values`.
4. Run again (`auto-build` step 4) and report whether phi now holds.
