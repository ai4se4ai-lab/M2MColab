# The checked builder: why the paper's Qwen2.5-Coder 7B admission does not reproduce, and how the code fixes it

*For updating the Builder (component 2) of [`emse-autom2m.tex`](emse-autom2m.tex) so that the paper matches
the code in [`src/autom2m/builder.py`](../src/autom2m/builder.py) and [`src/autom2m/loop.py`](../src/autom2m/loop.py).*

---

## 1. The issue

### What the paper claims

- Sec. 3.1 (component 2): the builder is a *Proposer* that "emits a typed team in a JSON schema (step A)"
  and a *Reviser* that "answers diagnostics (step C)"; Algorithm 1 proposes **one** team and, while the
  checker rejects it, asks for a revised **whole** team, for at most k_adm = 3 rounds.
- Sec. 4.2 places Qwen2.5-Coder 7B in the **strong** group of builders.
- Sec. 4.4 (RQ2, Fig. 6): "strong builders produce an admissible team in **85–98%** of sessions, mostly at
  the first proposal"; Table 8 reports AutoM2M success of **27.0%** on ClassEval for Qwen 7B.

### What the runs show

With the builder implemented as the paper describes it (called **v1** below), Qwen2.5-Coder 7B,
ClassEval, seed 1:

| Measure | Paper (Qwen 7B) | v1, real runs |
|---|---|---|
| AutoM2M sessions admitted (first full attempt, 99 tasks) | 85–98% | **49%** |
| Sessions admitted (admission-only study, 40 tasks) | 85–98% | **40%** (16/40) |
| AutoM2M task success (ClassEval) | 27.0% | 6–11% (refused sessions score 0) |

With v1, Qwen 7B behaves like the paper's **weak** builders (Granite, Code Llama, StarCoder2: 40–67%
admitted), not like a strong one.

### Why: the reviser does not converge

The checker is not at fault. Its diagnostics are correct and located (none of the refusals came from a
checker defect). The problem is how the builder uses them:

1. **The proposals are internally inconsistent.** Most violations are mismatches between the parts of
   one JSON object that the model wrote far apart: a rule binds or reads a feature its own view never
   declares (W1); a class declares mandatory features that its producing rule never binds (W3); a rule
   names a source variable it never introduced (W1); a hand-off forgets to list a view its rule matches
   (W1); a helper agent ("Validator") is given write rights on other agents' views (W2). Counted over
   the first-attempt sessions: W1 (undeclared features, unbound variables, wrong view) and W3
   (unbound mandatory features) make up about two thirds of all diagnostics; W2 about a fifth.

2. **Whole-team revision repeats the mistakes.** v1's reviser regenerates the entire team (≈1.5k
   output tokens) to fix a few local errors. In **40 of 42** revision rounds the revised team repeats a
   diagnostic of the previous round, and the number of violations barely falls (118 → 97 → 96 → 82 over
   rounds 0–3 for 19 refused sessions). A 7B model rewriting the whole object reproduces the same
   inconsistencies, and sometimes introduces new ones elsewhere.

3. **One proposal per session.** A single sample at temperature 0.6 is a high-variance draw. Yet the
   checker can evaluate a proposal in milliseconds (Sec. 4.4: "cheap enough to run on every candidate
   inside a builder's search loop"); the paper never uses that.

So the paper's admission figure for Qwen 7B assumes a builder that uses the checker better than
Algorithm 1 as written.

---

## 2. The fix: a *checked builder*

The principle is unchanged: **LLMs propose; programs decide.** Nothing below lets a program correct a
proposal, and the checker (Algorithm 3, W1–W6) remains the only authority on admission. The builder only
makes the proposals and revisions easier to get right and lets the checker choose among them. Three
mechanisms:

**(a) Schema-constrained decoding.** The JSON Schema of a typed team (the "typed-team metamodel" of
Sec. 3.3) is passed to the model's structured-output decoder (Ollama `format=<schema>`). Every proposal
then has the shape of a typed team: required keys, arrays and objects where they belong, validator ids and
done-clauses drawn from their enumerations. It can still be ill-typed, incomplete or defective; those
are the checker's business. Code: `TEAM_SCHEMA` in `builder.py`.

**(b) Checker-ranked proposals.** The builder samples up to k_prop proposals and checks each. It stops
at the first admitted one; otherwise it keeps the proposal with the fewest diagnostics. Each candidate
costs one check (milliseconds). The number of checks per session is recorded and reported (Fig. 7c).

**(c) Localized repair.** Each diagnostic is mapped to the JSON fragments it concerns: a rule
(`/handoffs/i/rules/j`) together with the classes it creates and reads and its hand-off's source list; a
view; the write rights (`/writes`); the goal and deliverable; the acceptance predicate (`/done`); the
agents (for W6). The reviser receives the whole team for reference, the diagnostics with their hints,
the fragments it may rewrite (each with its JSON pointer), and a **path catalogue**: for every diagnosed
rule, the navigation paths that type-check from its source variables (up to two references). It
answers with rewritten fragments only (schema-constrained, `FIX_SCHEMA`). The fragments are put back
into the team deterministically; parts that were not diagnosed are never regenerated. Up to k_rev repairs
are sampled per round and the best by the checker is kept. If no repair reduces the number of violations,
one whole-team revision (v1's reviser) is tried as a fallback. Code: `fragments_for`, `path_catalogue`,
`repair_prompt`, `apply_fixes` in `builder.py`; `AutoM2M._build_v2` in `loop.py`.

Defaults: k_prop = 3, k_rev = 2, k_adm = 3 (unchanged).

### Pseudocode (replaces lines 1–5 of Algorithm 1)

```latex
\begin{algorithm}[t]
\caption{\textsc{Build}: the checked builder (replaces steps A--C of Algorithm~\ref{alg:auto})}\label{alg:build}
\begin{algorithmic}[1]
\Require task $x$, goal model $M_0$; budgets $k_{\mathit{prop}}$ (proposals), $k_{\mathit{rev}}$ (repairs per round), $k_{\mathit{adm}}$ (rounds)
\State $\mathcal{C}\gets\emptyset$
\For{$i=1$ \textbf{to} $k_{\mathit{prop}}$} \Comment{Step A, schema-constrained}
  \State $\Team_i\gets\textsc{Propose}_{\mathit{schema}}(x,M_0)$;\ \ $\mathcal{C}\gets\mathcal{C}\cup\{(\Team_i,\textsc{Admit}(\Team_i))\}$ \Comment{Alg.~\ref{alg:admit}}
  \If{$\textsc{Admit}(\Team_i)=\emptyset$} \textbf{break} \EndIf
\EndFor
\State $(\Team,\mathit{diag})\gets\arg\min_{(\Team',d)\in\mathcal{C}}|d|$ \Comment{the checker ranks the candidates}
\For{$r=1$ \textbf{to} $k_{\mathit{adm}}$ \textbf{while} $\mathit{diag}\neq\emptyset$} \Comment{Step C, localized}
  \State $\Phi\gets\textsc{Fragments}(\Team,\mathit{diag})$ \Comment{JSON fragments the diagnostics name}
  \State $P\gets\textsc{Paths}(\Team,\Phi)$ \Comment{navigation paths that type-check in each diagnosed rule}
  \State $\mathcal{R}\gets\{\Team\lhd\textsc{Repair}_{\mathit{schema}}(\Team,\mathit{diag},\Phi,P)\mid j=1..k_{\mathit{rev}}\}$ \Comment{$\lhd$: replace fragments, keep the rest}
  \If{$\min_{\Team'\in\mathcal{R}}|\textsc{Admit}(\Team')|\ge|\mathit{diag}|$}
     \State $\mathcal{R}\gets\mathcal{R}\cup\{\textsc{Revise}_{\mathit{schema}}(\Team,\mathit{diag})\}$ \Comment{fallback: whole-team revision}
  \EndIf
  \State $\Team'\gets\arg\min_{\Team''\in\mathcal{R}}|\textsc{Admit}(\Team'')|$
  \If{$|\textsc{Admit}(\Team')|\le|\mathit{diag}|$} $\Team\gets\Team'$;\ $\mathit{diag}\gets\textsc{Admit}(\Team')$ \EndIf
\EndFor
\State \Return $\mathit{diag}=\emptyset$ ? $\Team$ : \textsc{NotAdmitted}$(\mathit{diag})$
\end{algorithmic}
\end{algorithm}
```

The same in plain pseudocode:

```
BUILD(x, M0):
    candidates = []
    repeat k_prop times:                                  # step A
        Θ = PROPOSE(x, M0, decoder constrained by TEAM_SCHEMA)
        d = ADMIT(Θ)                                      # Algorithm 3, milliseconds
        candidates.add((Θ, d))
        if d is empty: break
    (Θ, d) = candidate with the fewest diagnostics        # the checker ranks
    for round in 1..k_adm while d is not empty:           # step C
        F = FRAGMENTS(Θ, d)                               # pointer -> rule / class / writes / goal / done / agents
        P = PATHS(Θ, rules in F)                          # paths that type-check, ≤ 2 references
        R = []
        repeat k_rev times:
            fixes = REPAIR(Θ, d, F, P, decoder constrained by FIX_SCHEMA)
            R.add(Θ with the offered fragments replaced by fixes)    # other parts untouched
        if no member of R has fewer diagnostics than Θ:
            R.add(REVISE(Θ, d))                           # whole-team revision as a fallback
        Θ' = member of R with the fewest diagnostics
        if |ADMIT(Θ')| ≤ |d|: (Θ, d) = (Θ', ADMIT(Θ'))
    return Θ if d is empty else NOT_ADMITTED(d)
```

---

## 3. The measured effect

Admission-only study (`python -m evaluation.rq2.builder_ablation`): the same 40 ClassEval tasks
(ClassEval_0–39), the same seed, Qwen2.5-Coder 7B for every role, the same prompts, examples, validator
library and checker. Only the builder procedure differs. Raw data:
`results/rq2/builder_ablation__qwen2.5-coder_7b.jsonl`.

| | v1 (Algorithm 1 as written) | v2 (checked builder) |
|---|---|---|
| Sessions admitted | **16/40 (40%)** | **36/40 (90%)** |
| Admitted at the first proposal round | 15 | 32 |
| Admitted after revision | 1 | 4 |
| Median builder output tokens per session | 4,909 | 2,821 |
| Distinct admitted team shapes | 13 | 16 |
| Admitted teams copying a worked example's agents | 3/16 | 5/36 |

Paired by task: v2 admitted and v1 not on 22 tasks, the reverse on 2, both on 14, neither on 2 (exact
McNemar p = 3.6 × 10⁻⁵).

v2 is also **cheaper**, because a refused v1 session spends three whole-team revisions that do not
converge. Most of v2's gain comes from schema-constrained, checker-ranked proposals (32 admitted in the
proposal round against v1's 15); localized repair rescues 4 more. The four sessions v2 still refuses
fail on real composition defects that persist (an unanchored Example goal; views used but never
declared; a reference typed by the wrong class).

With v2, Qwen2.5-Coder 7B reaches **90%** admission, inside the paper's 85–98% for strong builders. The
paper's grouping of the model is then consistent, **provided the paper describes the builder as v2.**

---

## 4. What to change in the paper

**Status: applied to `emse-autom2m.tex`** (Fig. 1 sub-components and caption, component 2 and 4 text,
Algorithm 1, the new Algorithm *Build* (`alg:build`) with its explanatory paragraph, Sec. 3.3, Table 6
(AutoM2M and Typed-NC rows), the Procedure paragraph, a new RQ2 paragraph "Why the builder must use the
checker", Fig. 7c's definition of a check, the external-validity threat, the running example's
revision, and the data notice). The numbers in the RQ2 admission paragraph and in Figs. 6–7 other than
the ablation are still the paper's placeholders and must be regenerated.

One refinement since the measurement: for an unanchored or undelivered goal (W4), the reviser is also
offered the rules and views (`/handoffs`, `/views`), because such a goal is fixed by adding or changing the
rule that should carry it (as in the paper's running example). The 40-task numbers above were measured
just before this change; it affects at most the two v2 sessions refused on W4.

1. **Sec. 3.1, component 2 (Builder) and Fig. 1.** Describe the builder as *Proposer* (schema-constrained,
   k_prop samples ranked by the checker) and *Reviser* (localized repair of the diagnosed fragments with a
   path catalogue, whole-team revision as fallback). In Fig. 1, add the sub-component "Ranker (checker)"
   and rename "Reviser: answers diagnostics" to "Reviser: repairs diagnosed fragments".
2. **Algorithm 1.** Replace lines 1–5 (propose, admit, revise loop) by a call to `BUILD` (Algorithm
   *Build* above); the run/attribute/repair part is unchanged. State that every candidate is admitted
   only by Algorithm 3, so the principle "LLMs propose; programs decide" is unchanged.
3. **Sec. 3.3.** Where the paper says "the builder fills a JSON format whose JSON Schema plays the role
   of the typed-team metamodel", add that the schema also *constrains decoding*, so proposals always have
   the shape of a typed team; shape is necessary for checking, not sufficient for admission.
4. **Table 6 (AutoM2M row) and Sec. 4.2 (Procedure).** "Algorithm 1 with the checked builder
   (k_prop = 3, k_rev = 2, k_adm = 3), k_rep = 2, two-sided W4." Typed-NC keeps a single
   schema-constrained proposal and no checker, so it still isolates the format from the check.
5. **Sec. 4.4 (RQ2, admission, Fig. 6).** Report admission per model **for both builders**: the
   ablation separates what the checker contributes as a filter (v1) from what it contributes as a
   search signal (v2). Add the observation that whole-team revision repeats earlier diagnostics in 40 of
   42 rounds, the motivation for localized repair. Fig. 7c (checks per run) increases with v2:
   up to k_prop checks in step A and up to k_rev + 1 per round.
6. **Cost (Table 10, token shares).** Builder tokens per session *fall* with v2 for Qwen 7B (median 2.8k
   vs 4.9k), because refused v1 sessions spend three non-converging revisions.
7. **Threats to validity.** The builder procedure is a design choice that strongly affects admission;
   the ablation is reported. The admission study uses 40 ClassEval tasks and one seed.

---

## 5. Status and what is still open

- **Code.** `builder="v2"` is the default of the `AutoM2M` class (library, CLI, MCP `auto_solve`).
  `builder="v1"` reproduces Algorithm 1 as currently written. The experiment harness reads
  `AM2M_BUILDER` (default `v1` while the running schedule finishes, so that ClassEval seed 1 is not mixed).
  Tests: `tests/test_evaluation_pipeline.py::test_v2_builder_repairs_only_the_diagnosed_fragments`,
  `test_fragment_localisation`.
- **Not yet measured: the end-to-end effect.** More admitted teams do not by themselves mean more solved
  tasks: v2 admits teams v1 refused, and those may be harder to run. The AutoM2M condition has to be rerun
  with `AM2M_BUILDER=v2` to report Table 8 (success), Table 9 (composition-caused failures under the
  three codings) and Table 10 (precision of "done", cost) for the paper's builder.
- **Scope of the evidence.** One model (Qwen2.5-Coder 7B), ClassEval only, 40 tasks, one seed. The paper's
  claim about the other six models needs the same ablation per model.
