import { useState } from 'react'
import { REPO } from './Top'

const phases = [
  ['Done', 'Paper-aligned implementation', 'AgentHOT (compiler + runtime) and AutoM2M (lifter, builder, checker, attribution, repair) as in the paper: Example-level goal view, two-sided W4, stamps over footprint and validator reads, Algorithm 4, three repair modes.', 'v0.4.0'],
  ['Done', 'Full experiment harness', 'Eight conditions, RQ1 coding, RQ2 mutation / seeded defects / scale, RQ3 success, composition failures, "done" and cost, RQ4 fault injection and transcript baselines; one command regenerates every table and figure.', 'evaluation/'],
  ['Now', 'Complete the model matrix', 'Real runs exist for Qwen2.5-Coder 7B; the other six open-weight models of the design follow with the same scripts.', 'RQ1-RQ4'],
  ['Next', 'Builders trained on diagnostics', 'Use the checker inside the builder\'s search loop and train builders on its located diagnostics.', 'paper Sec. 5'],
  ['Next', 'Validators whose adequacy is checked', 'Stronger behaviour validators (example order, held-out checks) so that φ is precise and recall improves.', 'H3c'],
  ['Later', 'Beyond Python classes', 'Goal views for requirements, issues and repositories; validators beyond code execution; review loops under a bounded W5.', 'scope'],
]
const limits = [
  ['Adequacy of the forms', 'The checker verifies that the team fits together, not that its forms capture what the task needs.'],
  ['Validator strength', 'W4 requires an executable validator on every goal chain; it cannot require a good one, which bounds the precision of "done".'],
  ['Reasoning errors', 'Failures inside one agent\'s reasoning are outside any composition check.'],
  ['Expressiveness', 'Hand-off graphs are acyclic, LLMs fill attributes but do not create structure, and open-ended dialogue does not decompose into views and rules.'],
]

export function Roadmap() {
  return (
    <section className="section" id="roadmap">
      <div className="wrap">
        <div className="rule" />
        <p className="kicker">06 · Limits and roadmap</p>
        <h2 className="title">What AutoM2M does not solve, yet.</h2>
        <p className="lead">
          AutoM2M checks that a team fits together, not that its views capture the task or that its validators are strong.
          It is a research prototype for Python coding tasks, usable as a library, a CLI, an MCP server, a hosted service
          and the autom2m Claude Code plugin. Some limits are part of the idea; the rest are next steps.
        </p>
        <div className="grid-2 mt">
          {limits.map(([h, p]) => (
            <div className="card" key={h}>
              <h3 className="subhead" style={{ marginBottom: 6 }}>{h}</h3>
              <p style={{ margin: 0, color: 'var(--ink-2)', fontSize: 15, lineHeight: 1.6 }}>{p}</p>
            </div>
          ))}
        </div>
        <h3 className="subhead mt-l">Status and next steps</h3>
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
        <p className="caption">The code walkthrough (docs/agenthot-autom2m.md) maps every component of the paper to the code.</p>
      </div>
    </section>
  )
}

const BIB = `@misc{autom2m2026,
  title  = {When the Team Writes Itself: An Empirical Study of
            Composition Defects in LLM-Assembled Agent Teams and
            Their Prevention with Model Transformations},
  author = {Babaei, Majid},
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
          AutoM2M · AI4SE4AI Lab. A research prototype built on AgentHOT 0.2.0.
          <br />
          Code: <a href={REPO}>github.com/ai4se4ai-lab/M2MColab</a>.
        </div>
      </div>
    </footer>
  )
}
