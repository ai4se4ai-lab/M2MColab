import PipelineExplorer from '../flow/PipelineExplorer'

const remedies = [
  ['sampling', 'retry: the binding gets a fresh budget', 'mechanical'],
  ['footprint', 'widen the footprint by the added paths without which no replay passes', 'mechanical delta'],
  ['upstream', 're-sample the upstream value (and repair its own fault class)', 'mechanical'],
  ['specification, validator', 'the builder proposes a delta Δ from the fault report', 'LLM delta'],
]

export default function Explorer() {
  return (
    <section className="section" id="explorer">
      <div className="wrap">
        <div className="rule" />
        <p className="kicker">04 · Pipeline explorer</p>
        <h2 className="title">Watch AutoM2M build, check, run and repair a team.</h2>
        <p className="lead">
          Three walkthroughs of Algorithm 1: the paper's first proposal for TaskBoard (rejected, revised, admitted), its
          end-to-end runtime failure with attribution and repair, and the hand-written requirements team of the mutation
          study. Click any step, or use ← / → to move through it.
        </p>
        <div className="mt">
          <PipelineExplorer />
        </div>

        <div className="grid-2 mt-l" style={{ alignItems: 'start' }}>
          <div className="card">
            <h3 className="subhead">Attribution by construction</h3>
            <ol className="ladder">
              <li>
                <b>Validator fault?</b>
                <p>Rerun the failing validator three times on the same value (no LLM call). Unstable verdicts mean the check is unreliable, so no agent is blamed.</p>
              </li>
              <li>
                <b>Sampling fault?</b>
                <p>Replay the binding on its recorded footprint, up to n_rep = 3 draws, stopping at the first pass. If one passes, the composition was adequate and the LLM was unlucky or weak.</p>
              </li>
              <li>
                <b>Footprint fault?</b>
                <p>Widen the footprint by every path that follows at most j = 1, 2 references and replay. If that passes, the hand-off did not carry what the consumer needs (D2); the repair keeps only the paths without which no replay passes.</p>
              </li>
              <li>
                <b>Upstream fault?</b>
                <p>If a footprint value was itself LLM-written upstream, re-sample it and replay; then attribute that upstream binding, with its accepted value treated as failed.</p>
              </li>
              <li>
                <b>Specification fault</b>
                <p>Otherwise no value the footprint supports satisfies the prompt and validator.</p>
              </li>
            </ol>
            <p className="caption" style={{ marginTop: 0 }}>At most (h + 1 + |up(b)|)·n_rep + |up(b)| LLM calls per binding visited (Algorithm 4). Whether the classes agree with injected ground truth is RQ4.</p>
          </div>
          <div>
            <div className="table-wrap">
              <table className="data">
                <thead>
                  <tr>
                    <th>Fault class</th>
                    <th style={{ textAlign: 'left' }}>Remedy</th>
                    <th>Who</th>
                  </tr>
                </thead>
                <tbody>
                  {remedies.map(([k, r, w]) => (
                    <tr key={k}>
                      <td className="txt" style={{ fontFamily: 'var(--mono)', fontSize: 13 }}>{k}</td>
                      <td className="txt">{r}</td>
                      <td className="txt" style={{ textAlign: 'right' }}>{w}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <div className="card mt-s">
              <h3 className="subhead">Checked re-composition</h3>
              <p style={{ margin: 0, color: 'var(--ink-2)', fontSize: 15, lineHeight: 1.6 }}>
                Every delta is re-checked with W1-W6, then applied the cheapest safe way:
              </p>
              <ul style={{ margin: '10px 0 0', paddingLeft: 18, color: 'var(--ink-2)', fontSize: 15, lineHeight: 1.6 }}>
                <li><b>in place</b> when only rules, prompts, footprints or validators change; accepted values are kept unless their stamp goes stale;</li>
                <li><b>hot</b> when only forms, agents or hand-offs are added; newcomers get work for existing objects;</li>
                <li><b>rebuild</b> for anything else, losing accepted values, as a free-form builder always must.</li>
              </ul>
            </div>
          </div>
        </div>
      </div>
    </section>
  )
}
