import { useState } from 'react'

const conditions = ['single', 'free', 'critic', 'schema', 'typed_unchecked', 'autom2m', 'typed_ref']

type Table = { cols: string[]; rows: string[]; ours?: string; caption: string }
const tables: Record<string, Table> = {
  'End to end': {
    cols: ['condition', 'hidden tests pass', 'φ true', 'admission rounds', 'output tokens / task', 'time / task'],
    rows: conditions,
    ours: 'autom2m',
    caption: 'Same model, tools, worked example and turn budget for every condition. Hidden tests are used only for scoring.',
  },
  'Mutation study': {
    cols: ['defect · operator', 'mutants', 'G1 anchored', 'G1 naive', 'G2 anchored', 'G2 naive'],
    rows: [
      'D1 · delete a rule',
      'D1 · detach goal data from footprints',
      'D2 · break a footprint path',
      'D2 · drop a mandatory binding',
      'D3 · add a second writer to a view',
      'D3 · duplicate a producing rule',
      "D4 · add an agent's claim to φ",
      'D4 · drop an engine clause from φ',
      'D5 · remove a required tool',
    ],
    caption: 'Mutants of the admitted teams, checked with anchored and naive (class-level) W4, under G1 (criteria only) and G2 (criteria and deliverables).',
  },
  Attribution: {
    cols: ['method', 'agent', 'hand-off', 'binding', 'fault class', 'LLM calls'],
    rows: ['AutoM2M trace + replay', 'all-at-once (transcript)', 'step-by-step (transcript)', 'binary search (transcript)'],
    ours: 'AutoM2M trace + replay',
    caption: 'Injected upstream, footprint, specification, sampling and validator faults with known ground truth.',
  },
  Diagnosis: {
    cols: ['team builder', 'D1', 'D2', 'D3', 'D4', 'D5', 'reasoning', 'tool / other', 'κ'],
    rows: ['CaptainAgent', 'AutoAgents', 'MetaAgent', 'MAS-Zero'],
    caption: 'Two independent coders label the decisive cause of each failure with the D1-D5 codebook; disagreements are adjudicated.',
  },
}

const rqs = [
  {
    id: 'RQ1',
    name: 'Diagnosis',
    q: 'How often are failures of automatically assembled agent teams caused by composition defects, and by which ones?',
    m: ['share of failures whose decisive cause is D1-D5', 'per defect and per team builder', 'inter-coder agreement (κ)'],
  },
  {
    id: 'RQ2',
    name: 'Prevention',
    q: 'Can a deterministic checker over typed team specifications prevent composition defects, and at what cost?',
    m: ['detection of injected and natural defects', 'composition-caused failures per 100 runs', 'builder iterations, tokens, expressiveness'],
  },
  {
    id: 'RQ3',
    name: 'Attribution and repair',
    q: 'When a typed team fails, can its trace models locate the failure and support a checked repair that keeps accepted work?',
    m: ['accuracy against transcript-based attribution', 'fault-class accuracy per class', 'repair cost versus rebuilding'],
  },
]

export default function Results() {
  const [tab, setTab] = useState('End to end')
  const t = tables[tab]
  return (
    <section className="section" id="results">
      <div className="wrap">
        <div className="rule" />
        <p className="kicker">05 · Results</p>
        <h2 className="title">
          Results are being finalised <span className="ph-tag" style={{ fontSize: 12 }}>placeholder</span>
        </h2>
        <p className="lead">
          The study runs every condition on ClassEval and HumanEval+ across models and seeds. The figures and tables below
          show the layout of the final results; every value is a placeholder until the runs complete.
        </p>

        <div className="card mt">
          <div className="code-head">
            <span>Share of tasks whose hidden tests all pass</span>
            <span className="ph-tag">pending</span>
          </div>
          <div className="ph-chart" aria-label="Placeholder bar chart; results pending">
            <div className="ph-axis">
              <span>1.0</span>
              <span>0.75</span>
              <span>0.5</span>
              <span>0.25</span>
              <span>0</span>
            </div>
            <div className="ph-plot">
              {conditions.map((c) => (
                <div key={c} className={`ph-bar ${c === 'autom2m' ? 'ours' : ''}`}>
                  TBD
                </div>
              ))}
            </div>
          </div>
          <div className="ph-x">
            {conditions.map((c) => (
              <span key={c} className={c === 'autom2m' ? 'ours' : ''}>
                {c}
              </span>
            ))}
          </div>
          <div className="legend">
            <span><i style={{ borderColor: 'var(--h0)', borderStyle: 'dashed' }} />baselines and ablations</span>
            <span><i style={{ borderColor: 'var(--accent-2)', borderStyle: 'dashed' }} />AutoM2M</span>
          </div>
          <p className="caption">
            <b>Conditions.</b> single agent with fix rounds; free (CaptainAgent-style prose roles); critic (free plus an LLM
            critic for D1-D5); schema (one shared JSON board); typed_unchecked (typed format, no checker); autom2m; typed_ref
            (hand-written typed team with repair).
          </p>
        </div>

        <div className="tabs mt" role="tablist" aria-label="Results table">
          {Object.keys(tables).map((k) => (
            <button key={k} role="tab" className="tab" aria-selected={k === tab} onClick={() => setTab(k)}>
              {k}
            </button>
          ))}
        </div>
        <div className="table-wrap mt-s" role="tabpanel">
          <table className="data">
            <thead>
              <tr>
                {t.cols.map((c) => (
                  <th key={c}>{c}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {t.rows.map((r) => (
                <tr key={r} className={r === t.ours ? 'ours' : ''}>
                  <td>{r}</td>
                  {t.cols.slice(1).map((c) => (
                    <td key={c} className="tbd">
                      TBD
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <p className="caption">{t.caption}</p>

        <div className="grid-3 mt">
          {rqs.map((r) => (
            <div className="card rq-card" key={r.id}>
              <h4>
                <span className="mono" style={{ color: 'var(--accent-ink)' }}>{r.id}</span> {r.name}
                <span className="ph-tag">pending</span>
              </h4>
              <p className="q">{r.q}</p>
              <ul>
                {r.m.map((x) => (
                  <li key={x}>{x}</li>
                ))}
              </ul>
            </div>
          ))}
        </div>

        <div className="grid-2 mt">
          <div className="card">
            <h3 className="subhead">Checker cost</h3>
            <div className="table-wrap">
              <table className="data">
                <thead>
                  <tr>
                    <th>views in a chain team</th>
                    <th>10</th>
                    <th>100</th>
                    <th>1,000</th>
                    <th>3,000</th>
                  </tr>
                </thead>
                <tbody>
                  <tr>
                    <td>check time</td>
                    <td className="tbd">TBD</td>
                    <td className="tbd">TBD</td>
                    <td className="tbd">TBD</td>
                    <td className="tbd">TBD</td>
                  </tr>
                </tbody>
              </table>
            </div>
            <p className="caption">Each condition is polynomial in the size of the team, so checking can run on every candidate inside a builder's search loop.</p>
          </div>
          <div className="card">
            <h3 className="subhead">Is "done" more trustworthy?</h3>
            <div className="table-wrap">
              <table className="data">
                <thead>
                  <tr>
                    <th>run declared done by</th>
                    <th>runs</th>
                    <th>pass hidden tests</th>
                  </tr>
                </thead>
                <tbody>
                  <tr className="ours">
                    <td>φ holds (engine)</td>
                    <td>TBD</td>
                    <td>TBD</td>
                  </tr>
                  <tr>
                    <td>TERMINATE (agent)</td>
                    <td className="tbd">TBD</td>
                    <td className="tbd">TBD</td>
                  </tr>
                </tbody>
              </table>
            </div>
            <p className="caption">Compares how often an engine-decided "done" and an agent-declared "done" agree with the hidden tests.</p>
          </div>
        </div>
      </div>
    </section>
  )
}
