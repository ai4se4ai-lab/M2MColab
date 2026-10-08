---
name: change
description: Apply a change to an agentm2m team's source view (e.g. a tightened acceptance criterion or a new user story), preview exactly which downstream artifacts it obliges to be redone, then propagate it. Use when requirements or any upstream artifact of the team changes.
argument-hint: "<describe the change>"
---

# Change and propagate

Trace links make change impact exact: only bindings whose footprint the change touches are redone.

1. Call `team_status` and `model_show` for the view to change. Identify the owning agent (write
   rights are enforced; elements marked `engine_owned` cannot be edited: change their source instead).
2. Turn `$ARGUMENTS` into `model_edit` ops (`create` / `set` / `delete`, element keys like
   `Criterion#S2.1`). Do not apply them yet.
3. Call `impact` with `view`, `ops`, and `as_agent` to preview. Show the user:
   - obligations: existing values to be re-derived, with owning agent;
   - new bindings and created/deleted elements;
   - the upper bound on LLM calls.
   Mention that re-derived values which actually change may oblige further downstream bindings.
4. Apply with `model_edit` (same ops), then follow `/agentm2m:run` until nothing is pending.
5. Report which artifacts changed and confirm untouched ones were not redone: use `trace_query` on the
   changed element to show its downstream closure. Name only elements that appear in tool results;
   drafts or guard-excluded sources have no downstream elements at all.
