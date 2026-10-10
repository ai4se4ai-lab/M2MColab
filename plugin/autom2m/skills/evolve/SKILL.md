---
name: evolve
description: Add a new agent to a running agentm2m team (a higher-order transformation) - a new view it owns plus a hand-off from existing views - so it immediately receives obligations for every existing matching element, with no hand-written glue. Use when the user wants a new role, reviewer, or perspective on the team.
argument-hint: "<new role and what it should review or produce>"
---

# Evolve the team at runtime

1. Call `team_status` and pick the source view(s) the new agent reads.
2. Design, minimally:
   - a view spec (same shape as a view in `.agentm2m/team.yaml`, without `owner`): classes, attributes,
     cross-view references like `Arch.Operation`, and a root with one slot per created class;
   - a hand-off module creating that view from the source view(s). Keep it structural where possible;
     use `@llm(prompt, footprint)` only for values no expression can compute, with the smallest footprint
     and a `@check` validator. See `/agentm2m:author-handoff` for the rule language.
   For devteam, a ready-made Security Reviewer exists: `view_spec_file: rules/extra/SecurityReviewer.view.yaml`,
   `rule: rules/extra/Arch2Sec.agentm2m`, `view: Sec`, `handoff: Arch2Sec`, `agent: SecurityReviewer`.
3. Call `team_evolve` (pass `rule_text` to create a new rule file). The rule is validated before the team changes.
4. Run `/agentm2m:run`: the new agent gets an obligation for every existing match. Report how many it
   received and the result.
