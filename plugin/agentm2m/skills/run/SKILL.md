---
name: run
description: Run the agentm2m team to completion - execute all hand-offs, have the owning agents fill every pending @llm binding from its footprint only, and report whether the acceptance predicate phi holds. Use when the user asks to run, continue, or finish the team.
argument-hint: "[agent to restrict to]"
---

# Run the team to a fixpoint

The engine does all structure (matching, creating elements, references, trace links). You only
supply values for pending `@llm` bindings, and the engine accepts a value only if its `@check`
validator passes.

1. Call `run`. It reports what each hand-off created or deleted, escalations, and how many bindings are
   `pending` (ready to fill) or `blocked` (waiting on an upstream value that is not filled yet).
   If the backend is not `host`, the engine sampled the values itself: skip to step 4.
2. While bindings are pending:
   - Call `next_bindings` (optionally with `agent`) to see which agents own pending work.
   - Dispatch one `binding-worker` subagent per owning agent (in parallel when there are several),
     telling each: "You are the <Agent>. Fill all pending agentm2m bindings for agent <Agent>."
     If `$ARGUMENTS` names an agent, only dispatch that one.
   - For a handful of bindings you may instead fill them yourself, under the same rule: answer from
     the binding's `prompt` alone, never from other files or conversation context. Then call
     `submit_binding` with the bare value and the binding's `footprint_version`. On `rejected`, fix exactly what `reason` says and resubmit.
   - Call `run` again: accepted upstream values unblock downstream bindings.
3. Stop when `run` reports 0 pending. Blocked-but-never-ready bindings mean an upstream binding escalated.
4. Call `acceptance` and report:
   - phi (true/false), then per hand-off the elements created and bindings accepted.
   - Every escalation with its reason. Escalations are the team telling you where a human decision is
     needed; do not paper over them. Suggest the source change that would unblock each.
