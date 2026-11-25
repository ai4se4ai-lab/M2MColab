import RuntimeFlow from '../flow/RuntimeFlow'

export default function Idea() {
  return (
    <section className="section" id="idea">
      <div className="wrap">
        <div className="rule" />
        <p className="kicker">02 · The idea</p>
        <h2 className="title">Let the LLM propose the team. Let a checker decide.</h2>
        <p className="lead">
          One principle at two levels: <em>LLMs propose; programs decide</em>. AutoM2M's execution layer, AgentHOT
          (Agent Hand-Off Transformations: a compiler and a runtime), splits each hand-off: an LLM writes{' '}
          <em>values</em>, a deterministic engine owns the <em>structure</em>. AutoM2M applies the same split one level
          higher, to the team itself.
        </p>

        <div className="split mt">
          <div className="h" />
          <div className="h">The LLM writes</div>
          <div className="h">A deterministic program owns</div>
          <div className="row-h">AgentHOT</div>
          <div>values: a contract, a unit test, a method body, each from a declared footprint</div>
          <div>the structure of every hand-off: which objects exist and how they link, and which values are accepted</div>
          <div className="row-h ours">AutoM2M</div>
          <div>the team: views, hybrid hand-off rules, write rights, tools, goal obligations, φ</div>
          <div>the decision to admit that team (W1-W6), attribution of failures, and every repair</div>
        </div>

        <h3 className="subhead mt-l">What a typed team looks like when it runs</h3>
        <RuntimeFlow />
        <p className="caption">
          <b>The admitted typed DevTeam on AgentHOT</b> (Architect, Tester, Developer, as in the paper's running example).
          The task is lifted into a goal model of one Task, its Methods and their Examples. Hand-off rules match source
          objects and create targets; <code>@llm</code> marks the only values an LLM writes, each with a declared footprint
          and a validator. Stamps over footprint and validator reads tell the engine exactly what to redo after a change.
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
              <dd>Θ = (A, V, T, ω, κ, G, δ, φ): agents and tools, one view per agent, hybrid hand-offs with footprints and library validators, write rights, goal obligations (C, s, μ, F_C), a deliverable and an acceptance predicate, in a fixed JSON format.</dd>
              <dt>Revisions from diagnostics</dt>
              <dd>A rejected team comes back with exact, located violations, for up to k_adm = 3 rounds.</dd>
              <dt>Values inside the run</dt>
              <dd>Each stochastic binding sees only its footprint, and is re-sampled up to k = 3 times before it escalates.</dd>
              <dt>Team deltas</dt>
              <dd>When a fault is not mechanical (a specification or validator fault), the builder proposes a delta Δ.</dd>
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
              <dd>The compiler (a synthesis higher-order transformation) turns the admitted team into metamodels and rule modules; the runtime builds every object.</dd>
              <dt>Completion</dt>
              <dd>φ = cover(G) ∧ valid ∧ fresh ∧ noEsc (plus library clauses), evaluated on the models. No agent can say "done".</dd>
              <dt>Attribution and repair</dt>
              <dd>A failed clause is a trace lookup plus a bounded replay; every delta passes the same checker before it is applied in place, by extension or by rebuild.</dd>
            </dl>
          </div>
        </div>
      </div>
    </section>
  )
}
