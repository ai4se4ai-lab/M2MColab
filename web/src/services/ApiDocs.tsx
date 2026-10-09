import { useEffect, useState } from 'react'
import { ArrowSquareOut } from '@phosphor-icons/react'
import { api, origin } from './api'
import { Snippet } from './Copy'

type Op = { method: string; path: string; summary: string; tag: string; auth: boolean }
type OpenApi = { paths: Record<string, Record<string, { summary?: string; tags?: string[]; security?: unknown[] }>> }

export default function ApiDocs() {
  const [ops, setOps] = useState<Op[]>([])
  const [err, setErr] = useState<string | null>(null)
  const [embed, setEmbed] = useState(false)

  useEffect(() => {
    api<OpenApi>('/api/openapi.json')
      .then((spec) => {
        const out: Op[] = []
        for (const [path, methods] of Object.entries(spec.paths)) {
          for (const [method, op] of Object.entries(methods)) {
            out.push({ method: method.toUpperCase(), path, summary: op.summary ?? '', tag: op.tags?.[0] ?? '', auth: Boolean(op.security?.length) })
          }
        }
        setOps(out)
      })
      .catch((e) => setErr((e as Error).message))
  }, [])

  const url = origin()
  return (
    <div className="svc-page">
      <div className="row-between">
        <p className="lead" style={{ marginTop: 0 }}>
          Every MCP tool has a REST twin. Authenticate with <code>Authorization: Bearer &lt;key&gt;</code> (or <code>X-API-Key</code>); pick a project with{' '}
          <code>X-AgentM2M-Project</code> or <code>?project=</code>.
        </p>
      </div>
      <div className="btn-row" style={{ marginTop: 12 }}>
        <a className="btn primary" href="/api/docs" target="_blank" rel="noreferrer">
          <ArrowSquareOut size={18} /> Open interactive docs (Swagger)
        </a>
        <a className="btn" href="/api/redoc" target="_blank" rel="noreferrer">
          <ArrowSquareOut size={18} /> ReDoc
        </a>
        <a className="btn" href="/api/openapi.json" target="_blank" rel="noreferrer">
          OpenAPI JSON
        </a>
        <button type="button" className="btn" onClick={() => setEmbed((v) => !v)} aria-expanded={embed}>
          {embed ? 'Hide' : 'Show'} embedded docs
        </button>
      </div>
      {embed && <iframe className="docs-frame mt-s" src="/api/docs" title="Interactive API documentation" />}

      <h3 className="subhead mt">Endpoints</h3>
      {err && <p className="notice err">Could not load the OpenAPI document: {err}</p>}
      <div className="table-wrap">
        <table className="data">
          <thead>
            <tr>
              <th>Endpoint</th>
              <th style={{ textAlign: 'left' }}>What it does</th>
              <th>Key</th>
            </tr>
          </thead>
          <tbody>
            {ops.map((o) => (
              <tr key={o.method + o.path}>
                <td>
                  <span className={`meth ${o.method.toLowerCase()}`}>{o.method}</span> {o.path}
                </td>
                <td className="txt">{o.summary}</td>
                <td>{o.auth ? <span className="pill acc">required</span> : <span className="pill">public</span>}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <h3 className="subhead mt">The AutoM2M loop over REST</h3>
      <Snippet
        title="host mode: you are the builder and fill the values"
        text={`K="Authorization: Bearer <your API key>"
curl -X POST ${url}/api/auto/task  -H "$K" -H "content-type: application/json" -d '{"source": "<python skeleton>"}'
curl ${url}/api/auto/propose       -H "$K"        # builder prompt -> answer with a typed-team JSON
curl -X POST ${url}/api/auto/team  -H "$K" -H "content-type: application/json" -d '{"team": {...}}'   # W1-W6
curl -X POST ${url}/api/auto/run   -H "$K"        # fixpoint + phi; pending values
curl ${url}/api/auto/bindings      -H "$K"        # prompts to answer
curl -X POST ${url}/api/auto/bindings -H "$K" -H "content-type: application/json" \\
     -d '{"target_key": "...", "binding": "code", "value": "...", "footprint_version": "..."}'
curl ${url}/api/auto/deliverable   -H "$K"        # assembled code once phi holds`}
      />
    </div>
  )
}
