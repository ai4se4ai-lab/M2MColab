import { useState } from 'react'
import { REPO } from './Top'

const phases = [
  ['Phase 0', 'Make the current state solid', 'Commit the tree, regenerate every number from one command, finish the planned runs, add CI.', 'removes 41-43'],
  ['Phase 1', 'Separate the core from the benchmark', 'Domain packs, a validator registry, a tool vocabulary beyond exec, a library-side sandbox.', 'removes 6-11, 40'],
  ['Phase 2', 'More expressive teams', 'Structure-creating bindings, bounded review loops, relaxed W2, two-sided anchoring in W4.', 'removes 12-17'],
  ['Phase 3', 'A better builder', 'Backend parity, schema-constrained output, diverse examples, a small delta language.', 'removes 18-22, 34-35, 44'],
  ['Phase 4', 'Stronger validators', 'Validator adequacy checks, held-out checks, cost-aware re-validation, scaling fixes.', 'removes 2, 23-25'],
  ['Phase 5', 'Attribution you can trust', 'Adaptive replay, validator stability tests, minimal widening, side-effect-free replay.', 'removes 28-33'],
  ['Phase 6', 'AutoM2M in Claude Code', 'Workspace persistence, one team format, auto_check / auto_run MCP tools, host mode.', 'removes 27, 36-39'],
  ['Phase 7', 'Real-world pilots', 'Repository features, issue fixing, services from requirements, incidents, data questions.', 'removes 1, 4, 45'],
]

const limits = [
  ['Adequacy of the forms', 'The checker verifies that the team fits together, not that its forms capture what the task needs.'],
  ['Validator strength', 'W4 requires an executable validator on every goal path; it cannot require a good one.'],
  ['Reasoning errors', 'Failures inside one agent\'s reasoning are outside any composition check.'],
  ['Expressiveness', 'Debates and open-ended dialogue do not decompose naturally into forms and rules.'],
]

export function Roadmap() {
  return (
    <section className="section" id="roadmap">
      <div className="wrap">
        <div className="rule" />
        <p className="kicker">06 · Limits and roadmap</p>
        <h2 className="title">What AutoM2M does not solve, yet.</h2>
        <p className="lead">
          Today AutoM2M is a research prototype for Python coding tasks: a library and an experiment harness, not yet reachable
          from the Claude Code plugin. Some limits are part of the idea; most are engineering.
        </p>
        <div className="grid-2 mt">
          {limits.map(([h, p]) => (
            <div className="card" key={h}>
              <h3 className="subhead" style={{ marginBottom: 6 }}>{h}</h3>
              <p style={{ margin: 0, color: 'var(--ink-2)', fontSize: 15, lineHeight: 1.6 }}>{p}</p>
            </div>
          ))}
        </div>
        <h3 className="subhead mt-l">Plan for real-world projects</h3>
        <div className="road" tabIndex={0} aria-label="Roadmap phases, scroll horizontally">
          {phases.map(([ph, h, p, rm]) => (
            <div className="card" key={ph}>
              <div className="ph">{ph}</div>
              <h4>{h}</h4>
              <p>{p}</p>
              <div className="rm">{rm}</div>
            </div>
          ))}
        </div>
        <p className="caption">Numbers refer to the 45 limitations listed in the code walkthrough (docs/agentm2m-autom2m.md).</p>
      </div>
    </section>
  )
}

const BIB = `@misc{autom2m2026,
  title  = {When the Team Writes Itself: Composition Defects in
            Automatically Assembled LLM Agent Teams, and How Model
            Transformations Can Check Them},
  author = {TBD},
  year   = {2026},
  note   = {Preprint forthcoming},
  url    = {${REPO}}
}`

export function Cite() {
  const [copied, setCopied] = useState(false)
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(BIB)
      setCopied(true)
      setTimeout(() => setCopied(false), 1600)
    } catch {
      setCopied(false)
    }
  }
  return (
    <section className="section" id="cite">
      <div className="wrap">
        <div className="rule" />
        <p className="kicker">07 · Citation</p>
        <h2 className="title">
          BibTeX <span className="ph-tag" style={{ fontSize: 12 }}>placeholder</span>
        </h2>
        <div className="mt-s" style={{ display: 'flex', gap: 10, alignItems: 'center' }}>
          <button className="copy-btn" onClick={copy}>
            {copied ? 'copied' : 'copy to clipboard'}
          </button>
          <span className="muted small" aria-live="polite">{copied ? 'BibTeX copied.' : ''}</span>
        </div>
        <pre className="code bib mt-s">{BIB}</pre>
      </div>
    </section>
  )
}

export function Footer() {
  return (
    <footer className="foot">
      <div className="wrap">
        <div>
          AutoM2M · AI4SE4AI Lab. A research prototype built on AgentM2M 0.2.0.
          <br />
          Code: <a href={REPO}>github.com/ai4se4ai-lab/M2MColab</a>.
        </div>
      </div>
    </footer>
  )
}
