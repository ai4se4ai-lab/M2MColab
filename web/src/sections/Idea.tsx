import RuntimeFlow from '../flow/RuntimeFlow'

export default function Idea() {
  return (
    <section className="section" id="idea">
      <div className="wrap">
        <div className="rule" />
        <p className="kicker">02 · The idea</p>
        <h2 className="title">Let the LLM propose the team. Let a checker decide.</h2>
        <p className="lead">
          AgentM2M already splits each hand-off: an LLM writes <em>values</em>, a deterministic engine owns the{' '}
          <em>structure</em>. AutoM2M applies the same split one level higher, to the team itself.
        </p>

        <div className="split mt">
          <div className="h" />
          <div className="h">The LLM writes</div>
          <div className="h">A deterministic program owns</div>
          <div className="row-h">AgentM2M</div>
          <div>values: an API signature, a test oracle, a code body</div>
          <div>the structure of every hand-off: which objects exist and how they link</div>
          <div className="row-h ours">AutoM2M</div>
          <div>the team: agents, forms, rules, validators, goal</div>
          <div>the decision to admit that team, and every later repair</div>
        </div>

        <h3 className="subhead mt-l">What a typed team looks like when it runs</h3>
        <RuntimeFlow />
        <p className="caption">
          <b>The admitted DevTeam on the AgentM2M engine.</b> Each agent fills in its own form. Hand-off rules match source
          objects and create targets; <code>@llm</code> marks the only values an LLM writes, each with a declared footprint
          and a validator. Stamps tell the engine exactly what to redo after a change.
        </p>

        <div className="grid-2 mt">
          <div className="card side-card">
            <h3>
              <span className="dot llm" aria-hidden="true" />
              LLM side
            </h3>
            <div className="sub">what may be proposed</div>
            <dl>
              <dt>A typed team, not job descriptions</dt>
              <dd>Θ = (A, V, T, ω, κ, τ, G, φ): agents and tools, one form per agent, hand-offs, write rights, goal obligations and an acceptance predicate, in a fixed JSON format.</dd>
              <dt>Revisions from diagnostics</dt>
              <dd>A rejected team comes back with exact violations, for up to k_adm rounds.</dd>
              <dt>Values inside the run</dt>
              <dd>Each stochastic binding sees only its footprint, and is re-sampled up to k times before it escalates.</dd>
              <dt>Team deltas</dt>
              <dd>When a fault needs a new prompt, validator or hand-off, the builder proposes a delta Δ.</dd>
            </dl>
          </div>
          <div className="card side-card">
            <h3>
              <span className="dot eng" aria-hidden="true" />
              Deterministic side
            </h3>
            <div className="sub">what is decided</div>
            <dl>
              <dt>Admission</dt>
              <dd>W1-W6 on the team specification alone, in polynomial time, with no task data and no LLM call.</dd>
              <dt>Structure</dt>
              <dd>The admitted team compiles to ordinary AgentM2M rules; the unchanged runtime builds every object.</dd>
              <dt>Completion</dt>
              <dd>φ = cover(G) ∧ valid ∧ fresh ∧ noObl, evaluated on the models. No agent can say "done".</dd>
              <dt>Attribution and repair</dt>
              <dd>A failed clause is a trace lookup; every delta passes the same checker before it is applied.</dd>
            </dl>
          </div>
        </div>
      </div>
    </section>
  )
}
