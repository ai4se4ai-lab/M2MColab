import { useEffect, useMemo, useState } from 'react'
import { Play } from '@phosphor-icons/react'
import { api, type CheckResult } from './api'

type Sample = { name: string; team: unknown }

const CONDS: [string, string][] = [
  ['W1', 'well typed'],
  ['W2', 'one writer'],
  ['W3', 'complete'],
  ['W4', 'anchored coverage'],
  ['W5', 'engine decides done'],
  ['W6', 'right tools'],
]

const NOTES: Record<string, string> = {
  devteam_proposal: 'The seeded proposal: one instance of each defect D1–D5. The checker must report W1, W2, W4, W5, W6.',
  devteam_admitted_g2: 'The corrected DevTeam, goals and deliverables declared (G2). Admitted.',
  chakin_pilot: 'The hand-written AgentHOT pilot team: code is never behaviourally verified (two W4 violations).',
  classeval_reference: 'The reference team for Python classes: Tester writes tests, Developer writes code. Admitted.',
}

export default function Playground() {
  const [samples, setSamples] = useState<Sample[]>([])
  const [current, setCurrent] = useState<string>('')
  const [text, setText] = useState('')
  const [naive, setNaive] = useState(false)
  const [res, setRes] = useState<CheckResult | null>(null)
  const [err, setErr] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    api<{ samples: Sample[] }>('/api/teams/samples')
      .then((r) => {
        setSamples(r.samples)
        if (r.samples[0]) {
          setCurrent(r.samples[0].name)
          setText(JSON.stringify(r.samples[0].team, null, 2))
        }
      })
      .catch((e) => setErr(`Could not load the sample teams: ${(e as Error).message}`))
  }, [])

  const parseError = useMemo(() => {
    if (!text.trim()) return 'empty'
    try {
      JSON.parse(text)
      return null
    } catch (e) {
      return (e as Error).message
    }
  }, [text])

  const pick = (name: string) => {
    const s = samples.find((x) => x.name === name)
    if (!s) return
    setCurrent(name)
    setText(JSON.stringify(s.team, null, 2))
    setRes(null)
  }

  const run = async () => {
    setBusy(true)
    setErr(null)
    try {
      setRes(await api<CheckResult>('/api/auto/check', { method: 'POST', body: JSON.stringify({ team: text, naive }) }))
    } catch (e) {
      setErr((e as Error).message)
      setRes(null)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="svc-page">
      <p className="lead" style={{ marginTop: 0 }}>
        Paste a typed team, or start from a sample, and run the admission checker. It decides on the blueprint alone: no task data, no LLM call, no execution.
      </p>
      <div className="tabs mt-s" role="tablist" aria-label="Sample teams">
        {samples.map((s) => (
          <button key={s.name} type="button" className="tab mono" role="tab" aria-selected={current === s.name} onClick={() => pick(s.name)}>
            {s.name}
          </button>
        ))}
      </div>
      {current && NOTES[current] && <p className="caption">{NOTES[current]}</p>}
      <div className="grid-2 wide-left mt-s">
        <div className="card">
          <div className="code-head">
            <span>typed team (JSON)</span>
            <span className={parseError ? 'bad-txt' : 'ok-txt'}>{parseError ? 'invalid JSON' : 'valid JSON'}</span>
          </div>
          <textarea
            className="editor"
            spellCheck={false}
            value={text}
            onChange={(e) => {
              setText(e.target.value)
              setCurrent('')
            }}
            aria-label="Typed team JSON"
          />
          <div className="row-between mt-s">
            <label className="check">
              <input type="checkbox" checked={naive} onChange={(e) => setNaive(e.target.checked)} /> naive (class-level) W4
            </label>
            <button type="button" className="btn primary" onClick={run} disabled={busy || !text.trim()}>
              <Play size={16} weight="fill" /> {busy ? 'Checking…' : 'Run checker'}
            </button>
          </div>
        </div>
        <div className="card" aria-live="polite">
          <h3 className="subhead">Verdict</h3>
          {err && <p className="notice err">{err}</p>}
          {!res && !err && <p className="muted">Run the checker to see the verdict.</p>}
          {res && (
            <>
              <div className={`verdict ${res.admitted ? 'ok' : 'bad'}`}>{res.admitted ? 'ADMITTED' : `REJECTED · ${res.diagnostics.length} violation(s)`}</div>
              <div className="cond-grid">
                {CONDS.map(([id, name]) => {
                  const bad = res.violated.includes(id)
                  return (
                    <div key={id} className={`cond ${bad ? 'bad' : 'ok'}`}>
                      <b>{id}</b>
                      <span>{name}</span>
                    </div>
                  )
                })}
              </div>
              {res.diagnostics.length > 0 && (
                <ul className="diag-list">
                  {res.diagnostics.map((d, i) => (
                    <li key={i}>
                      <span className="pill neg">{d.cond}</span> <code>{d.element}</code>
                      <div>{d.message}</div>
                    </li>
                  ))}
                </ul>
              )}
              {res.warnings.length > 0 && (
                <ul className="diag-list">
                  {res.warnings.map((d, i) => (
                    <li key={i}>
                      <span className="pill warn">{d.cond} warning</span> <code>{d.element}</code>
                      <div>{d.message}</div>
                    </li>
                  ))}
                </ul>
              )}
            </>
          )}
        </div>
      </div>
    </div>
  )
}
