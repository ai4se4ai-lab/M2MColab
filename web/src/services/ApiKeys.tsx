import { useEffect, useState } from 'react'
import { Key, Trash, CheckCircle } from '@phosphor-icons/react'
import { ago, api, ApiError, origin, saveKey, savedKey, type KeyRecord } from './api'
import { CopyButton, Snippet } from './Copy'

export default function ApiKeys() {
  const [label, setLabel] = useState('')
  const [email, setEmail] = useState('')
  const [key, setKey] = useState<string | null>(savedKey())
  const [fresh, setFresh] = useState(false)
  const [rec, setRec] = useState<KeyRecord | null>(null)
  const [msg, setMsg] = useState<{ kind: 'ok' | 'err'; text: string } | null>(null)
  const [busy, setBusy] = useState(false)
  const [paste, setPaste] = useState('')

  const verify = async (k: string) => {
    try {
      const r = await api<KeyRecord>('/api/keys/me', { key: k })
      setRec(r)
      return true
    } catch (e) {
      setRec(null)
      if (e instanceof ApiError && e.status === 401) {
        setMsg({ kind: 'err', text: 'This key is not valid (revoked or unknown).' })
      } else {
        setMsg({ kind: 'err', text: `Could not verify the key: ${(e as Error).message}` })
      }
      return false
    }
  }

  useEffect(() => {
    const k = savedKey()
    const t = k ? window.setTimeout(() => verify(k), 0) : undefined
    return () => window.clearTimeout(t)
  }, [])

  const create = async (ev: React.FormEvent) => {
    ev.preventDefault()
    setBusy(true)
    setMsg(null)
    try {
      const r = await api<{ key: string; record: KeyRecord }>('/api/keys', {
        method: 'POST',
        body: JSON.stringify({ label, email }),
      })
      setKey(r.key)
      setRec(r.record)
      setFresh(true)
      saveKey(r.key)
      setMsg({ kind: 'ok', text: 'Key created. Copy it now: the server keeps only its hash.' })
    } catch (e) {
      setMsg({ kind: 'err', text: (e as Error).message })
    } finally {
      setBusy(false)
    }
  }

  const revoke = async () => {
    if (!key || !window.confirm('Revoke this key? Clients using it stop working immediately.')) return
    try {
      await api('/api/keys/me', { method: 'DELETE', key })
      setMsg({ kind: 'ok', text: 'Key revoked.' })
    } catch (e) {
      setMsg({ kind: 'err', text: (e as Error).message })
    }
    saveKey(null)
    setKey(null)
    setRec(null)
    setFresh(false)
  }

  const use = async (ev: React.FormEvent) => {
    ev.preventDefault()
    const k = paste.trim()
    if (!k) return
    setMsg(null)
    if (await verify(k)) {
      setKey(k)
      saveKey(k)
      setFresh(false)
      setPaste('')
      setMsg({ kind: 'ok', text: 'Key verified and remembered in this browser.' })
    }
  }

  const shown = key && fresh ? key : '<your API key>'
  const url = origin()

  return (
    <div className="svc-page">
      <div className="grid-2">
        <div className="card">
          <h3 className="subhead">
            <Key size={18} /> Get an API key
          </h3>
          <p className="caption" style={{ marginTop: 0 }}>
            No account needed during the research preview. Keys are free and unlimited; paid tiers will add server-side
            LLM work later (see Pricing).
          </p>
          <form onSubmit={create} className="form">
            <label>
              Label <span className="muted">(to recognise the key)</span>
              <input value={label} onChange={(e) => setLabel(e.target.value)} maxLength={80} placeholder="e.g. laptop" />
            </label>
            <label>
              Email <span className="muted">(optional, for future plan changes)</span>
              <input type="email" value={email} onChange={(e) => setEmail(e.target.value)} maxLength={120} placeholder="you@example.org" />
            </label>
            <button className="btn primary" type="submit" disabled={busy}>
              {busy ? 'Creating…' : 'Create API key'}
            </button>
          </form>
          <form onSubmit={use} className="form mt-s">
            <label>
              Already have a key?
              <input value={paste} onChange={(e) => setPaste(e.target.value)} placeholder="am2m_…" autoComplete="off" />
            </label>
            <button className="btn" type="submit">
              Use this key
            </button>
          </form>
          {msg && (
            <p className={`notice ${msg.kind}`} role="status">
              {msg.text}
            </p>
          )}
        </div>

        <div className="card">
          <h3 className="subhead">Your key</h3>
          {key && rec ? (
            <>
              <div className="key-box">
                <code className="key-text">{fresh ? key : `${rec.prefix}${'•'.repeat(18)}`}</code>
                <CopyButton text={key} label="Copy key" />
              </div>
              <table className="kv">
                <tbody>
                  <tr>
                    <th>Status</th>
                    <td>
                      <span className="pill pos">
                        <CheckCircle size={12} weight="fill" /> active
                      </span>
                    </td>
                  </tr>
                  <tr>
                    <th>Tier</th>
                    <td>{rec.tier}</td>
                  </tr>
                  <tr>
                    <th>Label</th>
                    <td>{rec.label || <span className="muted">–</span>}</td>
                  </tr>
                  <tr>
                    <th>Created</th>
                    <td>{new Date(rec.created * 1000).toLocaleString()}</td>
                  </tr>
                  <tr>
                    <th>Requests</th>
                    <td>
                      {rec.request_count} · last {ago(rec.last_used)}
                    </td>
                  </tr>
                </tbody>
              </table>
              <p className="caption">This browser remembers the key so the other tabs can use it. Revoking it stops every client using it.</p>
              <button type="button" className="btn danger" onClick={revoke}>
                <Trash size={16} /> Revoke key
              </button>
            </>
          ) : (
            <p className="muted">No key in this browser yet. Create one, or paste an existing key.</p>
          )}
        </div>
      </div>

      <h3 className="subhead mt">Use it</h3>
      <div className="grid-2">
        <Snippet title="Claude Code (remote MCP)" text={`claude mcp add --transport http agenthot ${url}/mcp \\\n  --header "Authorization: Bearer ${shown}"`} />
        <Snippet
          title="Any MCP client (JSON config)"
          text={JSON.stringify({ mcpServers: { agenthot: { type: 'http', url: `${url}/mcp`, headers: { Authorization: `Bearer ${shown}` } } } }, null, 2)}
        />
        <Snippet
          title="REST: set the task"
          text={`curl -X POST ${url}/api/auto/task \\\n  -H "Authorization: Bearer ${shown}" -H "content-type: application/json" \\\n  -d '{"source": "def add(a, b):\\n    \\"\\"\\"Return a + b.\\n    >>> add(1, 2)\\n    3\\n    \\"\\"\\"\\n"}'`}
        />
        <Snippet title="REST: status and φ" text={`curl ${url}/api/auto/status -H "Authorization: Bearer ${shown}"`} />
      </div>
    </div>
  )
}
