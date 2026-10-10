const audit: { q: string; v: string; share: string; indent?: boolean; tbd?: boolean }[] = [
  { q: 'Roles whose prompt names any teammate', v: '0 / 356', share: '0.0%' },
  { q: 'Roles whose prompt states what the role must output', v: '0 / 356', share: '0.0%' },
  { q: 'Roles with any hand-off-like phrase (loose pattern)', v: '32 / 356', share: '9.0%' },
  { q: 'Teams whose plan assigns any step to a named role', v: '0 / 126', share: '0.0%' },
  { q: 'Teams with an output format (for the whole team only)', v: '126 / 126', share: '100%' },
  { q: 'Failed runs ended by an agent declaring TERMINATE', v: '82 / 126', share: '65.1%' },
  { q: 'of which the terminating agent was a verifier', v: '43 / 82', share: '52.4%', indent: true },
]

const defects = [
  { id: 'D1', name: 'Coverage gap', plain: 'nobody owns it', what: 'Part of the task is owned by no agent, or a goal is never checked against anything that read it.', where: 'Plan steps 1-4 belong to nobody.', by: 'W4' },
  { id: 'D2', name: 'Hand-off mismatch', plain: 'lost in hand-off', what: 'What a producer emits is not what its consumer needs, in content, reference or form.', where: 'No role states its output.', by: 'W1, W3' },
  { id: 'D3', name: 'Ownership conflict', plain: 'too many cooks', what: 'Two agents act on the same artefact, or none knows it is theirs.', where: 'Who parses the street numbers?', by: 'W2' },
  { id: 'D4', name: 'Unverifiable completion', plain: 'done because someone said so', what: '"Done" is a claim in the conversation, not a checked property.', where: 'The verifier declares success; the run ends wrong.', by: 'W5' },
  { id: 'D5', name: 'Capability mismatch', plain: 'wrong person for the job', what: 'Work is routed to an agent that lacks the needed tool or skill.', where: 'A search-oriented verifier checks spreadsheet code.', by: 'W6' },
  { id: 'C', name: 'Opaque attribution', plain: 'who broke it?', what: 'A failure cannot be traced to the defect or the agent that caused it.', where: 'Only a transcript is left to read.', by: 'Step E' },
]

export default function Problem() {
  return (
    <section className="section" id="problem">
      <div className="wrap">
        <div className="rule" />
        <p className="kicker">01 · The problem</p>
        <h2 className="title">Team builders compose agents whose interfaces are prose.</h2>
        <p className="lead">
          A team builder is an architect: it composes a system out of components (agents) and connectors (hand-offs). But
          every agent is a job description, every hand-off is free text, and the assembled team runs without any check that
          its parts fit. Software architecture calls this <em>architectural mismatch</em>. We call it the{' '}
          <em>composition gap</em>.
        </p>

        <div className="grid-2 mt">
          <div className="card">
            <div className="code-head">
              <span>Raw input · CaptainAgent, one GAIA task</span>
            </div>
            <div className="code" style={{ whiteSpace: 'pre-wrap' }}>
              <div>Team: Excel_Expert, BusinessLogic_Expert,</div>
              <div>      DataVerification_Expert (+ Computer_terminal)</div>
              <div> </div>
              <div>Excel_Expert: "Expert in analyzing and processing data</div>
              <div>  from Excel files ... pivot tables, data cleaning ..."</div>
              <div>BusinessLogic_Expert: "... translating them into</div>
              <div>  actionable data logic ..."</div>
              <div>DataVerification_Expert: "... employing Bing Search API</div>
              <div>  ... vigilantly verifying the accuracy ..."</div>
              <div> </div>
              <div className="c">## Plan for solving the task         (shared brief)</div>
              <div className="hl">1. Load the provided Excel file and read the client data.</div>
              <div className="hl">2. Identify the street addresses of the clients.</div>
              <div className="hl">3. Determine which addresses are even-numbered.</div>
              <div className="hl">4. Count the number of clients ...</div>
              <div className="c">## Output format</div>
              <div>The number of clients receiving the sunset awning design.</div>
            </div>
            <p className="caption">
              <b>(a)</b> Each role says what the agent is <em>good at</em>. None says what it receives, what it must
              produce, or for whom; no plan step names who performs it. The verification expert declared the result verified,
              and the team terminated with a wrong answer.
            </p>
          </div>
          <div className="card flush" style={{ display: 'flex', flexDirection: 'column' }}>
            <div style={{ padding: '18px 20px 6px' }}>
              <div className="code-head">
                <span>Audit · 126 auto-built teams</span>
              </div>
            </div>
            <div style={{ overflowX: 'auto' }}>
              <table className="data">
                <thead>
                  <tr>
                    <th>Question asked of the specification</th>
                    <th>Count</th>
                    <th>Share</th>
                  </tr>
                </thead>
                <tbody>
                  {audit.map((r) => (
                    <tr key={r.q}>
                      <td className="txt">{r.indent ? <span className="indent">{r.q}</span> : r.q}</td>
                      <td className={r.tbd ? 'tbd' : ''}>{r.v}</td>
                      <td className={r.tbd ? 'tbd' : ''}>{r.share}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <p className="caption" style={{ padding: '0 20px 18px' }}>
              <b>(b)</b> Who&amp;When, Algorithm-Generated split, 356 generated roles. The audit asks only what each
              specification <em>states</em>, with a deterministic script. The only interface any team declares is the format
              of its final answer.
            </p>
          </div>
        </div>

        <h3 className="subhead mt-l">Five composition defects, and one consequence</h3>
        <div className="grid-3">
          {defects.map((d) => (
            <div className="card defect" key={d.id}>
              <div className="tag">
                <span className="code-id">{d.id}</span>
                <span className="pill acc">ruled out by {d.by}</span>
              </div>
              <h4>{d.name}</h4>
              <div className="plain">"{d.plain}"</div>
              <p>{d.what}</p>
              <div className="where">In the spec above: {d.where}</div>
            </div>
          ))}
        </div>
        <p className="caption">
          The mapping from published failure modes (MAST, AgentAsk's edge-level errors, Who&amp;When) to defects is
          analytical; RQ1 estimates how often each defect is the <em>decisive</em> cause of a failure.
        </p>
      </div>
    </section>
  )
}
