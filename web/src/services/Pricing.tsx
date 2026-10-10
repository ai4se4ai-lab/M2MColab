import { useEffect, useState } from 'react'
import { Check } from '@phosphor-icons/react'
import { api, type Tier } from './api'

export default function Pricing({ onKeys }: { onKeys: () => void }) {
  const [tiers, setTiers] = useState<Tier[]>([])
  const [note, setNote] = useState('')
  const [err, setErr] = useState<string | null>(null)

  useEffect(() => {
    api<{ tiers: Tier[]; note: string }>('/api/pricing')
      .then((r) => {
        setTiers(r.tiers)
        setNote(r.note)
      })
      .catch((e) => setErr((e as Error).message))
  }, [])

  return (
    <div className="svc-page">
      {note && <p className="lead" style={{ marginTop: 0 }}>{note}</p>}
      {err && <p className="notice err">Could not load the plans: {err}</p>}
      <div className="price-grid mt-s">
        {tiers.map((t) => (
          <div key={t.id} className={`card price ${t.available ? 'current' : 'later'}`}>
            <div className="row-between">
              <h3 className="subhead" style={{ margin: 0 }}>
                {t.name}
              </h3>
              {t.available ? <span className="pill pos">available now</span> : <span className="pill">coming later</span>}
            </div>
            <div className="price-num">
              {t.price}
              <small> {t.period}</small>
            </div>
            <p className="caption" style={{ marginTop: 6 }}>
              {t.summary}
            </p>
            <ul className="feat">
              {t.features.map((f) => (
                <li key={f}>
                  <Check size={14} weight="bold" /> {f}
                </li>
              ))}
            </ul>
            {t.available ? (
              <button type="button" className="btn primary" onClick={onKeys}>
                Get a free API key
              </button>
            ) : (
              <button type="button" className="btn" disabled>
                Not available yet
              </button>
            )}
          </div>
        ))}
      </div>
    </div>
  )
}
