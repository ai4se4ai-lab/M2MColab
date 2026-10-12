import { useState } from 'react'
import W4Flow from '../flow/W4Flow'

const conds = [
  {
    id: 'W1',
    name: 'Hand-offs are well typed and stratified',
    plain: 'Every rule reads only its source views and writes only features its target class declares; no LLM-written value steers structure.',
    precise: 'Every guard, binding and footprint path type-checks; stochastic bindings target primitive attributes and use a library validator with correctly typed arguments; no guard or structural binding reads a feature written by a stochastic binding (stratification).',
    rules: 'D2, formal part',
    example: "The Developer's prompt refers to d.returnType, which the Design view does not declare.",
    diag: "W1  Design2Impl.body: footprint path 'd.returnType':\n        Design!MethodDesign has no feature 'returnType'",
  },
  {
    id: 'W2',
    name: 'Every artefact has one writer',
    plain: 'No two agents write the same view, every non-goal view has an owner, and every class is created by one rule only.',
    precise: 'The write rights ω(a) are pairwise disjoint and cover V \\ {MM0}, so owner(T) is a single agent; every class is created by at most one rule. Conservative: rules with provably disjoint guards could share a class.',
    rules: 'D3',
    example: 'The Developer and the Tester both write the Test view.',
    diag: "W2  view Test written by ['Developer', 'Tester']; needs exactly one writer",
  },
  {
    id: 'W3',
    name: 'Targets are complete',
    plain: 'Every object a rule creates gets all its mandatory features, and every reference it makes resolves.',
    precise: 'Every mandatory feature of a created class is bound exactly once; every reference binding typed in the target view is resolvable (some rule maps the yielded source class to the expected class); every reference typed in a source view yields objects of that type. A guard equating two such references to the same goal object is a join.',
    rules: 'D2, content part',
    example: 'No W3 defect is seeded in the proposal. The mutation study drops mandatory bindings: 22 mutants, all rejected.',
    diag: '',
  },
  {
    id: 'W4',
    name: 'Every goal is anchored',
    plain: 'The producer of a checked value must have seen the goal, and its check must test against the goal.',
    precise: 'On the feature-level data-flow graph with production edges (expressions, footprints) and check edges (validator reads), every anchor feature of a checked obligation reaches the same behaviour-checked feature along production edges, and along production edges followed by one check edge; source classes on the chain are total. Delivered obligations reach the deliverable; every view is reachable from MM0 and reaches a check.',
    rules: 'D1',
    example: 'No rule reads an Example, so example E2.2 cannot influence anything that is checked.',
    diag: 'W4  goal (Example, all, checked): anchor features {call, expected}\n        reach no behaviour-checked value',
  },
  {
    id: 'W5',
    name: 'The engine decides completion',
    plain: '"Done" is made of clauses the engine evaluates, never of what an agent says.',
    precise: 'φ contains the four engine clauses cover(G), valid, fresh, noEsc, optionally library clauses such as running the public examples on the deliverable, and no agent claim; the hand-off graph is acyclic.',
    rules: 'D4',
    example: 'The proposal ends the run when the Tester says "ALL TESTS PASS".',
    diag: "W5  done-clause Tester.says('ALL TESTS PASS') is not an engine clause",
  },
  {
    id: 'W6',
    name: 'Work goes to agents that can do it',
    plain: "An owner's validators run in its sandbox, so it must have every tool they need.",
    precise: 'For every stochastic binding b, τ(b) ⊆ κ(owner(b)); production itself uses no tools.',
    rules: 'D5',
    example: 'smoke_test executes code, which the Tester cannot do.',
    diag: "W6  Impl2Run.verdict needs ['exec']; writer Tester has []",
  },
]

export default function Checker() {
  const [i, setI] = useState(3)
  const c = conds[i]
  return (
    <section className="section" id="checker">
      <div className="wrap">
        <div className="rule" />
        <p className="kicker">03 · The checker</p>
        <h2 className="title">Six conditions, checked before anything runs.</h2>
        <p className="lead">
          Each condition is a set comparison or a reachability question on the typed team alone: no task data, no LLM
          call, no execution, polynomial time. Each rules out one composition defect. The diagnostics below are the
          checker's actual output on the paper's illustrative first proposal (Listing 5): one violation per seeded defect,
          and no other.
        </p>

        <div className="tabs mt" role="tablist" aria-label="Admission condition">
          {conds.map((x, k) => (
            <button key={x.id} role="tab" className="tab mono" aria-selected={k === i} onClick={() => setI(k)}>
              {x.id}
            </button>
          ))}
        </div>
        <div className="grid-2 mt-s" role="tabpanel" aria-label={`${c.id}: ${c.name}`}>
          <div className="card">
            <div className="tag" style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 8 }}>
              <span className="mono" style={{ fontSize: 22, color: 'var(--accent-ink)' }}>{c.id}</span>
              <span className="pill acc">rules out {c.rules}</span>
            </div>
            <h3 className="subhead" style={{ marginTop: 6 }}>{c.name}</h3>
            <p style={{ margin: 0, fontStyle: 'italic', color: 'var(--ink-2)' }}>{c.plain}</p>
            <p className="caption">{c.precise}</p>
          </div>
          <div className="card">
            <div className="code-head">
              <span>In the first proposal (Listing 5)</span>
            </div>
            <p style={{ margin: '0 0 12px', color: 'var(--ink-2)', fontSize: 15 }}>{c.example}</p>
            {c.diag ? (
              <div className="code" style={{ whiteSpace: 'pre-wrap' }}>
                <div className="bad">{c.diag}</div>
              </div>
            ) : (
              <div className="code c" style={{ whiteSpace: 'pre-wrap' }}>
                REJECTED: 5 violation(s)  (W1, W2, W4, W5, W6)
              </div>
            )}
          </div>
        </div>

        <h3 className="subhead mt-l">Why W4 must be anchored, on both sides</h3>
        <W4Flow />

        <div className="grid-2 mt">
          <div className="card side-card">
            <h3>
              <span className="dot eng" aria-hidden="true" />
              What admission guarantees
            </h3>
            <div className="sub">if the run stops with φ true</div>
            <dl>
              <dt>Every goal is tested by something that read it</dt>
              <dd>Each in-scope goal object has a descendant whose behavioural validator passed, and whose footprint and validator both read data derived from it (Proposition 2).</dd>
              <dt>Nothing is written twice</dt>
              <dd>No object has two producing rules and no view two writers.</dd>
              <dt>Every LLM answer passed a check</dt>
              <dd>Each value was produced from a recorded footprint, under a stamp over its footprint and validator reads, and passed its validator; only engine clauses decided completion.</dd>
            </dl>
          </div>
          <div className="card side-card">
            <h3>
              <span className="dot" style={{ background: 'var(--neg)' }} aria-hidden="true" />
              What it does not
            </h3>
            <div className="sub">limits stated up front</div>
            <dl>
              <dt>That the forms fit the task</dt>
              <dd>A missing concept is a defect only if the goal view declares it as an obligation.</dd>
              <dt>That validators are strong</dt>
              <dd>W4 requires a behavioural validator on every goal chain, not a good one: a test that runs, fails on a stub and asserts every example can still encode a wrong reading.</dd>
              <dt>That agents reason well</dt>
              <dd>A well-typed team of weak agents still fails. It fails in fewer, and more diagnosable, ways.</dd>
            </dl>
          </div>
        </div>
      </div>
    </section>
  )
}
