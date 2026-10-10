import { useState } from 'react'
import data from '../data/results.json'

// Every number here comes from web/src/data/results.json, generated from the
// experiment logs by `python -m evaluation.analysis.analyze && python -m evaluation.analysis.web_export`.
// A study that has not produced data yet renders as "pending".

/* eslint-disable @typescript-eslint/no-explicit-any */
const D: any = data
const CONDS = ['single', 'single_gate', 'free', 'critic', 'schema', 'typed_nc', 'autom2m', 'typed_ref']
const LAB: Record<string, string> = {
  single: 'Single', single_gate: 'Single-Gate', free: 'Free', critic: 'Critic', schema: 'Schema',
  typed_nc: 'Typed-NC', autom2m: 'AutoM2M', typed_ref: 'Typed-Ref',
}
const BENCH: Record<string, string> = { classeval: 'ClassEval', humanevalplus: 'HumanEval+' }

const pct = (x: any, d = 1) => (typeof x === 'number' && isFinite(x) ? `${(100 * x).toFixed(d)}%` : '—')
const num = (x: any, d = 1) => (typeof x === 'number' && isFinite(x) ? x.toFixed(d) : '—')
const ci = (c: any) => (Array.isArray(c) && typeof c[0] === 'number' ? `[${(100 * c[0]).toFixed(0)}, ${(100 * c[1]).toFixed(0)}]` : '')

function Pending({ what }: { what: string }) {
  return <p className="caption"><span className="ph-tag">pending</span> {what} has not produced data yet.</p>
}

function Bars({ bench }: { bench: string }) {
  const mean = D.success?.[bench]?.mean ?? {}
  const vals = CONDS.map((c) => (typeof mean[c] === 'number' ? mean[c] : null))
  if (vals.every((v) => v === null)) return <Pending what={`${BENCH[bench]} success`} />
  const W = 640, H = 210, pad = 30, bw = (W - pad) / CONDS.length
  return (
    <svg viewBox={`0 0 ${W} ${H + 40}`} role="img" aria-label={`Success per condition on ${BENCH[bench]}`} style={{ width: '100%', height: 'auto' }}>
      {[0, 0.25, 0.5, 0.75, 1].map((t) => (
        <g key={t}>
          <line x1={pad} x2={W} y1={H - t * (H - 10)} y2={H - t * (H - 10)} stroke="var(--line)" strokeWidth={1} />
          <text x={pad - 6} y={H - t * (H - 10) + 4} fontSize={10} textAnchor="end" fill="var(--muted)">{t * 100}</text>
        </g>
      ))}
      {vals.map((v, i) => {
        const x = pad + i * bw + 6
        const h = v === null ? 0 : v * (H - 10)
        const ours = CONDS[i] === 'autom2m'
        return (
          <g key={CONDS[i]}>
            <rect x={x} y={H - h} width={bw - 12} height={h} rx={3} fill={ours ? 'var(--accent)' : 'var(--h0)'} />
            <text x={x + (bw - 12) / 2} y={H - h - 5} fontSize={11} textAnchor="middle" fill="var(--ink-2)">
              {v === null ? '—' : (100 * v).toFixed(1)}
            </text>
            <text x={x + (bw - 12) / 2} y={H + 16} fontSize={11} textAnchor="middle" fill={ours ? 'var(--accent-ink)' : 'var(--ink-2)'} fontWeight={ours ? 600 : 400}>
              {LAB[CONDS[i]]}
            </text>
          </g>
        )
      })}
    </svg>
  )
}

function Rq1() {
  const r = D.rq1 ?? {}
  const names: Record<string, string> = { ww_auto: 'Who&When, auto-built (CaptainAgent)', ours: 'Ours (Free, Critic)', ww_hand: 'Who&When, hand-crafted (Magentic-One)' }
  const keys = Object.keys(names).filter((k) => r[k])
  if (!keys.length) return <Pending what="RQ1 failure coding" />
  return (
    <table className="data">
      <thead><tr><th>source</th><th>coded failures</th><th>decisive cause D1-D5 [95% CI]</th><th>incl. secondary</th><th>D1+D2</th><th>reasoning</th><th>κ (coders)</th></tr></thead>
      <tbody>
        {keys.map((k) => (
          <tr key={k}>
            <td>{names[k]}</td><td>{r[k].n}</td><td>{pct(r[k].share)} {ci(r[k].ci)}</td><td>{pct(r[k].with_secondary)}</td>
            <td>{pct((r[k].by_code?.D1 ?? 0) + (r[k].by_code?.D2 ?? 0))}</td><td>{pct(r[k].by_code?.reasoning)}</td><td>{num(r[k].kappa, 2)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}

function Rq2() {
  const m = D.mutation
  const ind = D.independent
  const adm = D.admission
  return (
    <>
      {m ? (
        <table className="data">
          <thead><tr><th>mutation operator</th><th>n</th><th>path-only G1/G2</th><th>one-sided G1/G2</th><th>two-sided G1/G2</th></tr></thead>
          <tbody>
            {Object.entries(m.groups ?? {}).map(([k, v]: [string, any]) => (
              <tr key={k}>
                <td>{k}</td><td>{v.n}</td><td>{v['path-only|G1']} / {v['path-only|G2']}</td><td>{v['one-sided|G1']} / {v['one-sided|G2']}</td>
                <td>{v['two-sided|G1']} / {v['two-sided|G2']}</td>
              </tr>
            ))}
            <tr className="ours"><td>total (mutants rejected)</td><td>{m.n}</td><td>{m.total?.['path-only|G1']} / {m.total?.['path-only|G2']}</td>
              <td>{m.total?.['one-sided|G1']} / {m.total?.['one-sided|G2']}</td><td>{m.total?.['two-sided|G1']} / {m.total?.['two-sided|G2']}</td></tr>
          </tbody>
        </table>
      ) : <Pending what="The mutation analysis" />}
      {ind ? (
        <table className="data mt-s">
          <thead><tr><th>detector</th><th>seeded defects detected</th><th>false alarms on clean teams</th></tr></thead>
          <tbody>
            {Object.entries(ind).map(([k, v]: [string, any]) => (
              <tr key={k} className={k === 'checker_two_sided' ? 'ours' : ''}>
                <td>{k.replace('checker_two_sided', 'checker, two-sided W4').replace('checker_one_sided', 'checker, one-sided W4').replace('critic:', 'LLM critic ')}</td>
                <td>{v.detected}/{v.n} ({pct(v.rate)})</td><td>{v.false_alarms}/{v.n_clean} ({pct(v.fa_rate)})</td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : <Pending what="Independent defect seeding" />}
      {adm?.by_model ? (
        <table className="data mt-s">
          <thead><tr><th>builder model</th><th>sessions</th><th>admitted</th><th>at first proposal</th><th>first violation of rejected proposals</th></tr></thead>
          <tbody>
            {Object.entries(adm.by_model).map(([k, v]: [string, any]) => (
              <tr key={k}><td>{k}</td><td>{v.n}</td><td>{pct(v.admitted)}</td><td>{pct(v.first_round)}</td>
                <td>{Object.entries(adm.first_violation ?? {}).map(([c, s]: [string, any]) => `${c} ${pct(s, 0)}`).join(' · ')}</td></tr>
            ))}
          </tbody>
        </table>
      ) : <Pending what="Builder admission" />}
    </>
  )
}

function Rq3() {
  const cf = D.compfail
  const dn = D.done?.classeval
  return (
    <>
      {cf?.per100 ? (
        <table className="data">
          <thead><tr><th>condition</th><th>composition-caused failures / 100 runs (executed only)</th><th>refused = composition</th><th>refused = other</th></tr></thead>
          <tbody>
            {['free', 'critic', 'schema', 'typed_nc', 'autom2m', 'typed_ref'].filter((c) => cf.per100[c]).map((c) => (
              <tr key={c} className={c === 'autom2m' ? 'ours' : ''}>
                <td>{LAB[c]}</td><td>{num(cf.per100[c]['i|all'])}</td><td>{num(cf.per100[c]['ii|all'])}</td><td>{num(cf.per100[c]['iii|all'])}</td>
              </tr>
            ))}
            {cf.rr && <tr><td>risk ratio AutoM2M / Free</td><td>{num(cf.rr['i|all'], 2)}</td><td>{num(cf.rr['ii|all'], 2)}</td><td>{num(cf.rr['iii|all'], 2)}</td></tr>}
          </tbody>
        </table>
      ) : <Pending what="Coding of composition-caused failures (RQ3)" />}
      {dn ? (
        <table className="data mt-s">
          <thead><tr><th>condition (ClassEval)</th><th>precision of “done” [95% CI]</th><th>lift</th><th>recall</th><th>F1</th><th>median output tokens</th><th>median seconds</th></tr></thead>
          <tbody>
            {CONDS.filter((c) => dn[c]).map((c) => (
              <tr key={c} className={c === 'autom2m' ? 'ours' : ''}>
                <td>{LAB[c]}</td><td>{pct(dn[c].precision)} {ci(dn[c].precision_ci)}</td><td>{num(dn[c].lift, 2)}</td><td>{pct(dn[c].recall)}</td>
                <td>{pct(dn[c].f1)}</td><td>{num(dn[c].median_tokens, 0)}</td><td>{num(dn[c].median_seconds, 0)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : <Pending what="The reliability-of-done analysis" />}
    </>
  )
}

function Rq4() {
  const r = D.rq4 ?? {}
  const ref = r.ref
  const tm = r.transcript_methods
  return (
    <>
      {ref || tm ? (
        <table className="data">
          <thead><tr><th>method (injected faults, reference team)</th><th>agent</th><th>step / binding</th><th>fault class (macro)</th><th>LLM calls</th></tr></thead>
          <tbody>
            {ref && (
              <>
                <tr className="ours"><td>AutoM2M, adaptive replay (≤ 3)</td><td>{pct(ref.agent_ok)}</td><td>{pct(ref.responsible_found)}</td><td>{pct(ref.macro_n3)}</td><td>{num(ref.calls_n3)}</td></tr>
                <tr><td>AutoM2M, one replay</td><td>{pct(ref.agent_ok)}</td><td>{pct(ref.responsible_found)}</td><td>{pct(ref.macro_n1)}</td><td>{num(ref.calls_n1)}</td></tr>
              </>
            )}
            {tm && Object.entries(tm).map(([k, v]: [string, any]) => (
              <tr key={k}><td>{k.replace(/_/g, ' ')} (transcript)</td><td>{pct(v.agent)}</td><td>{pct(v.step)}</td><td>{pct(v.macro_class)}</td><td>{num(v.calls)}</td></tr>
            ))}
          </tbody>
        </table>
      ) : <Pending what="Fault injection (RQ4)" />}
      {r.repair ? (
        <table className="data mt-s">
          <thead><tr><th>after a failed AutoM2M run</th><th>passes hidden tests</th><th>median output tokens</th></tr></thead>
          <tbody>
            <tr className="ours"><td>one checked delta (repair)</td><td>{pct(r.repair.repair_success)}</td><td>{num(r.repair.repair_tokens_median, 0)}</td></tr>
            <tr><td>rebuild from scratch</td><td>{pct(r.repair.rebuild_success)}</td><td>{num(r.repair.rebuild_tokens_median, 0)}</td></tr>
          </tbody>
        </table>
      ) : <Pending what="Repair versus rebuild" />}
    </>
  )
}

const RQS = [
  { id: 'RQ1', name: 'Diagnosis', q: 'How often is the decisive cause of a failure of an automatically assembled team a composition defect, and which one?', body: Rq1 },
  { id: 'RQ2', name: 'Prevention', q: 'Does the checker detect composition defects, and can LLM builders produce admissible typed teams?', body: Rq2 },
  { id: 'RQ3', name: 'Effect', q: 'What do typing and checking do to task success, composition-caused failures, the reliability of “done”, and cost?', body: Rq3 },
  { id: 'RQ4', name: 'Attribution and repair', q: 'Does trace-based attribution locate and classify faults better than transcript-based methods, and does checked repair beat rebuilding?', body: Rq4 },
]

export default function Results() {
  const [bench, setBench] = useState('classeval')
  const [rq, setRq] = useState(0)
  const R = RQS[rq]
  const counts = D.run_counts ?? {}
  const total = Object.values(counts).reduce((a: number, b: any) => a + Object.values(b ?? {}).reduce((x: number, y: any) => x + (y as number), 0), 0)
  return (
    <section className="section" id="results">
      <div className="wrap">
        <div className="rule" />
        <p className="kicker">05 · Results</p>
        <h2 className="title">Results from real runs</h2>
        <p className="lead">
          The study design crosses eight conditions, ClassEval and HumanEval+, seven open-weight models and three seeds. The
          numbers below are generated from the logs of the runs completed so far ({total} runs; models:{' '}
          {(D.models ?? []).join(', ') || 'none yet'}; regenerated {D.generated}). Other models of the design are pending.
        </p>

        <div className="card mt">
          <div className="code-head">
            <span>Share of runs whose hidden tests all pass, mean over seeds</span>
            <span>
              {Object.keys(BENCH).map((b) => (
                <button key={b} className="tab" aria-selected={b === bench} onClick={() => setBench(b)} style={{ marginLeft: 6 }}>{BENCH[b]}</button>
              ))}
            </span>
          </div>
          <Bars bench={bench} />
          <p className="caption">
            <b>Conditions.</b> Single (one agent, two self-repair rounds); Single-Gate (best-of-n up to AutoM2M's median
            token budget, accepted when the public examples pass); Free (CaptainAgent-style prose roles, group chat);
            Critic (Free plus an LLM critic for D1-D5); Schema (PatchBoard-style shared JSON board); Typed-NC (typed teams on
            AgentHOT without checker or repair); AutoM2M; Typed-Ref (a hand-written typed team with checker and repair).
          </p>
        </div>

        <div className="tabs mt" role="tablist" aria-label="Research question">
          {RQS.map((x, k) => (
            <button key={x.id} role="tab" className="tab mono" aria-selected={k === rq} onClick={() => setRq(k)}>
              {x.id}
            </button>
          ))}
        </div>
        <div className="card mt-s" role="tabpanel">
          <h3 className="subhead" style={{ marginTop: 0 }}>
            <span className="mono" style={{ color: 'var(--accent-ink)' }}>{R.id}</span> {R.name}
          </h3>
          <p className="caption" style={{ marginTop: 0 }}>{R.q}</p>
          <div className="table-wrap"><R.body /></div>
        </div>

        <div className="grid-2 mt">
          <div className="card">
            <h3 className="subhead">Checker cost</h3>
            {D.scale?.chain ? (
              <div className="table-wrap">
                <table className="data">
                  <thead><tr><th>views</th>{Object.keys(D.scale.chain).map((n) => <th key={n}>{Number(n).toLocaleString()}</th>)}</tr></thead>
                  <tbody>
                    <tr><td>chain</td>{Object.values(D.scale.chain).map((v: any, i) => <td key={i}>{(1000 * v).toFixed(1)} ms</td>)}</tr>
                    {D.scale.dag && <tr><td>DAG (fan-in 3)</td>{Object.values(D.scale.dag).map((v: any, i) => <td key={i}>{(1000 * v).toFixed(1)} ms</td>)}</tr>}
                  </tbody>
                </table>
              </div>
            ) : <Pending what="The scale study" />}
            <p className="caption">Median of 10 runs. Every condition is polynomial in the size of the team (Proposition 1); builder-generated teams have three to six views.</p>
          </div>
          <div className="card">
            <h3 className="subhead">Who&amp;When specification audit</h3>
            {D.audit ? (
              <table className="data">
                <tbody>
                  <tr><td>roles that name a teammate</td><td>{D.audit.roles_naming_teammate} / {D.audit.roles}</td></tr>
                  <tr><td>roles that state their output</td><td>{D.audit.roles_stating_output} / {D.audit.roles}</td></tr>
                  <tr><td>plans assigning a step to a named role</td><td>{D.audit.plans_assigning_step_to_role} / {D.audit.teams}</td></tr>
                  <tr><td>failed runs ended by an agent's TERMINATE</td><td>{D.audit.runs_terminated_by_agent} / {D.audit.teams}</td></tr>
                </tbody>
              </table>
            ) : <Pending what="The audit" />}
            <p className="caption">A deterministic script asks only what each of the 126 CaptainAgent team specifications states.</p>
          </div>
        </div>
      </div>
    </section>
  )
}
