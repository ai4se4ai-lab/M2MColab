---
name: author-handoff
description: Write or modify AgentHOT view metamodels (team.yaml) and hand-off rule modules (.agenthot files with @llm/@check bindings and helpers.py validators). Use when designing a custom team, adding a view or hand-off, or fixing a rule that fails validation.
argument-hint: "<what the hand-off should do>"
---

# Authoring views and hand-offs

## View (in `.agenthot/team.yaml` under `views:`)

```yaml
Arch:
  owner: Architect                 # the only agent that may edit this view
  classes:
    Component: {attributes: [name]}
    Operation:
      attributes: [name, signature]          # string by default; also {count: int}, {ok: boolean}
      references:
        component: Component                 # same view, single, non-containment
        story: Req.UserStory                 # cross-view reference (View.Class)
        params: {type: Param, many: true, containment: true}
  root: {class: ArchModel, slots: {components: Component, operations: Operation}}
  seed: {...}                      # optional initial model, same shape as model_edit values
```
Every class that a rule matches or creates needs an `id` or `name` attribute: that is its trace key.
Every class a rule creates needs a root slot.

## Hand-off module (`.agenthot/rules/<Name>.agenthot`, listed under `handoffs:`)

```
module Req2Arch;
create OUT : Arch from IN : Req;            -- several sources: from IN1 : A, IN2 : B
uses 'helpers.py';                           -- OCL-callable Python helpers/validators

rule Story2Operation {
  from s : Req!UserStory (s.status = #accepted)          -- source pattern + guard
  to  op : Arch!Operation (
    name      <- s.id.toOpName(),                         -- structural (deterministic)
    component <- s.epic,                                  -- source element -> resolved via trace
    signature <- @llm('Derive an API signature ...', s.criteria),   -- stochastic: prompt, footprint
    @check signature.parses() and signature.params()->notEmpty() )  -- validator for the binding above
}
```
Rules of thumb:
- Anything an expression can compute is a structural binding. Use `@llm` only for values no expression
  can produce, and only for primitive attributes, never references.
- The footprint (second `@llm` argument) is ALL the source data the LLM sees and what change impact is
  computed from. Keep it minimal and specific: a smaller footprint means fewer, cheaper re-derivations.
- Tell the prompt the exact answer format; validators judge the bare value.
- `self <- @llm(...)` is a Lift binding: the answer is a JSON object written onto several attributes,
  rejected unless every key is an attribute of the target class.
- OCL subset: navigation `a.b`, `and or not`, `= <> < > <= >=`, `'str'`, `#literal`, and
  `->select(x | ...) ->collect ->forAll ->exists ->notEmpty ->isEmpty ->size`. Anything else is a
  helper function in `helpers.py`, called as `x.helper(args)`.
- Validators in `helpers.py` return `True`, or `Rejected("why")` from `agenthot.engine.validators`
  so the reason reaches the next attempt. Run generated code only in a subprocess with a timeout.
  `helpers.py` is executed by the engine: only use trusted code.

## Check your work
After editing, the plugin hook re-validates automatically; also call `team_validate` and fix every
error. Then call `impact` to see how many bindings the change creates before running.
