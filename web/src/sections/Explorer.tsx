import PipelineExplorer from '../flow/PipelineExplorer'

const remedies = [
  ['sampling', 'retry the binding with a fresh budget', 'mechanical'],
  ['upstream', 'delete the upstream stamp, making it an obligation', 'mechanical'],
  ['footprint', 'add the widening paths that worked to the footprint', 'mechanical delta'],
  ['specification, coverage, validator', 'the builder proposes a revised team', 'LLM delta'],
]

export default function Explorer() {
  return (
    <section className="section" id="explorer">
      <div className="wrap">
        <div className="rule" />
        <p className="kicker">04 · Pipeline explorer</p>
        <h2 className="title">Watch AutoM2M build, check, run and repair a team.</h2>
        <p className="lead">
          Three walkthroughs of the AutoM2M loop: the seeded proposal from the paper, an illustrative runtime failure, and the
          hand-written AgentM2M pilot team. Click any step, or use ← / → to move through it.
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
                <p>Run the failing validator twice on the same value. Different verdicts mean the check is unreliable, so no agent is blamed.</p>
              </li>
              <li>
                <b>Sampling fault?</b>
                <p>Replay the binding on its stamped footprint. If a sample passes, the composition was adequate and the LLM was unlucky.</p>
              </li>
              <li>
                <b>Footprint fault?</b>
                <p>Widen the footprint by one hop and replay. If that passes, the hand-off did not carry what the consumer needs (D2).</p>
              </li>
              <li>
                <b>Upstream fault?</b>
                <p>If a footprint value was itself LLM-written, re-sample it and replay; follow the trace model backwards.</p>
              </li>
              <li>
                <b>Specification fault</b>
                <p>Otherwise the prompt or validator cannot be satisfied from this footprint.</p>
              </li>
            </ol>
            <p className="caption" style={{ marginTop: 0 }}>At most 2r LLM calls per binding visited. Whether the classes agree with ground truth is RQ3.</p>
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
