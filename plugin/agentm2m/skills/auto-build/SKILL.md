---
name: auto-build
description: Build, check and run an AutoM2M team for a Python task - you act as the team builder (typed-team JSON), the W1-W6 checker admits or rejects it, then the engine runs it and the owning agents fill each value from its footprint until phi holds. Use when the user wants a checked agent team to implement a class or function.
argument-hint: "<python file with the class or function skeleton>"
---

# Build a checked team for a task (AutoM2M, host mode)

AutoM2M splits the work: **you propose the team, a deterministic checker decides whether it may run,
and the engine decides when it is done.** You never declare success yourself; phi does.

1. **Task.** Read the skeleton named in `$ARGUMENTS` (or ask for one): a class whose methods to implement
   are stubs (docstring with `>>>` examples + `pass` / `...` / `raise NotImplementedError`), or one stub
   function. Call `auto_task_set` with the full source. Report the methods it found.
2. **Propose.** Call `auto_propose`. Dispatch the `team-builder` subagent with the returned prompt, or
   answer it yourself: ONE typed-team JSON object, nothing else.
3. **Admit.** Call `auto_submit_team` with that JSON.
   - `admitted: false`: every entry of `diagnostics` names a W1-W6 condition and the element to fix. Call
     `auto_propose` (it now returns a *revise* prompt with those diagnostics), fix exactly those, resubmit.
     Stop after 3 rejected rounds and show the diagnostics to the user.
   - `admitted: true`: report the agents, views and hand-offs.
4. **Run.** Call `auto_run`, then while `pending > 0`:
   - Call `auto_next_bindings` to see which agents own work. Dispatch one `auto-binding-worker` per
     owning agent ("You are the <Agent>. Fill all pending AutoM2M bindings for agent <Agent>."), or fill a
     few yourself under the same rule: answer ONLY from the binding's `prompt`.
   - Call `auto_run` again: accepted upstream values unblock downstream bindings.
5. **Done?** Call `auto_status`. If `phi` is true, call `auto_deliverable` and show the code (offer to
   write it into the user's file). If phi is false and nothing is pending, run the `diagnose` skill.

Report: admission rounds, the team, how many values were accepted, phi, and every escalation with its reason.
