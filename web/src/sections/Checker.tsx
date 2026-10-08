import { useState } from 'react'
import W4Flow from '../flow/W4Flow'

const conds = [
  {
    id: 'W1',
    name: 'Hand-offs are well typed',
    plain: 'Every rule reads only what its source forms contain and writes only what its target form declares.',
    precise: 'Source and target classes belong to the hand-off\'s views; every binding names a feature of its target class with a conforming type; every footprint path type-checks; stochastic bindings target primitive attributes and each has a validator.',
    rules: 'D2, formal part',
    example: 'The Developer\'s LLM would be prompted with op.returnType, which no one produces.',
    diag: "W1  Op2Edit.body: footprint path 'op.returnType': Arch!Operation has no feature 'returnType'",
  },
  {
    id: 'W2',
    name: 'Every artefact has exactly one writer',
    plain: 'No two agents write the same form, every non-goal form has an owner, and every kind of object is created by one rule only.',
    precise: 'The write sets ω(a) are pairwise disjoint and cover V \\ {M0}; each class has at most one producing hand-off and rule. Conservative: rules with provably disjoint guards are still rejected.',
    rules: 'D3',
    example: 'The Developer and the Tester both write the Test form.',
    diag: "W2  view Test written by ['Developer', 'Tester']; needs exactly one writer",
  },
  {
    id: 'W3',
    name: 'Targets are complete',
    plain: 'Every object a rule creates gets all its mandatory fields, and every link it makes points to something that exists.',
    precise: 'Each feature with lower bound ≥ 1 is bound exactly once; every reference binding is resolvable, i.e. some rule maps the source class to the expected target class.',
    rules: 'D2, content part',
    example: 'Delete Epic2Component in the chakin team: operations still refer to their epic\'s component, which is now never created. A dangling reference.',
    diag: '',
  },
  {
    id: 'W4',
    name: 'Every obligation reaches a check that reads it',
    plain: 'Every goal must flow, through the hand-offs, into a value a behavioural validator checks; every agent\'s form must lie on such a flow.',
    precise: 'On the feature-level data-flow graph, some feature of each goal class reaches a feature written under an executable behavioural validator (anchored coverage), and every view is reachable from the goal view and reaches a checked view.',
    rules: 'D1',
    example: 'No binding reads any feature of Criterion, so no criterion ever influences a checked value.',
    diag: "W4  goal obligation (Criterion, c.story.status = 'accepted'): no behavioural validator reads data derived from Criterion",
  },
  {
    id: 'W5',
    name: 'The engine decides completion',
    plain: '"Done" is made of things the engine can check, never of what an agent says.',
    precise: 'φ is a conjunction from a fixed vocabulary: cover(G), valid, fresh, noObl; and the view graph is acyclic.',
    rules: 'D4',
    example: 'The proposal ends the run when the Tester says "ALL TESTS PASS".',
    diag: "W5  done-clause Tester.says('ALL TESTS PASS') is not engine-checkable",
  },
  {
    id: 'W6',
    name: 'Work goes to agents that can do it',
    plain: 'An agent is given an LLM-written value only if it has the tools that value\'s prompt and validator need.',
    precise: 'For every stochastic binding b owned by agent a, τ(b) ⊆ κ(a).',
    rules: 'D5',
    example: 'verdict.runs() needs code execution, which the Tester lacks.',
    diag: "W6  Edit2TestRun.verdict needs ['exec']; owner Tester has []",
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
          Each condition is a count, a set comparison or a reachability question on the team blueprint. Each rules out one
          composition defect. A violation is returned to the builder as a diagnostic that names the exact place to fix.
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
              <span>In the seeded proposal</span>
            </div>
            <p style={{ margin: '0 0 12px', color: 'var(--ink-2)', fontSize: 15 }}>{c.example}</p>
            {c.diag ? (
              <div className="code" style={{ whiteSpace: 'pre-wrap' }}>
                <div className="bad">{c.diag}</div>
              </div>
            ) : (
              <div className="code c" style={{ whiteSpace: 'pre-wrap' }}>
                No W3 defect is seeded in the proposal; the example comes from the mutation study.
              </div>
            )}
          </div>
        </div>

        <h3 className="subhead mt-l">Why W4 must be anchored</h3>
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
              <dd>Each in-scope goal object reaches a value whose behavioural validator passed and whose footprint reads data derived from it.</dd>
              <dt>Nothing is written twice</dt>
              <dd>No object has two producing rules and no view two writers.</dd>
              <dt>Every LLM answer passed a check</dt>
              <dd>Each value was produced from a recorded footprint, under a stamp, and passed its validator; only engine clauses decided completion.</dd>
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
              <dd>W4 requires a behavioural validator on every goal path, not a good one.</dd>
              <dt>That agents reason well</dt>
              <dd>A well-typed team of weak agents still fails. It fails in fewer, and more diagnosable, ways.</dd>
            </dl>
          </div>
        </div>
      </div>
    </section>
  )
}
