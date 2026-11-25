import { useCallback, useEffect, useRef, useState } from 'react'
import { ArrowClockwise, Plugs } from '@phosphor-icons/react'
import { ago, api, duration, mcpHandshake, origin, savedKey, type Health } from './api'
import { Snippet } from './Copy'

const POLL_MS = 5000

type Probe = { ok: boolean; ms: number; at: number; error?: string }

export default function Status() {
  const [health, setHealth] = useState<Health | null>(null)
  const [probe, setProbe] = useState<Probe | null>(null)
  const [lastUp, setLastUp] = useState<number | null>(null)
  const [mcp, setMcp] = useState<string | null>(null)
  const timer = useRef<number | undefined>(undefined)

  const check = useCallback(async () => {
    const t0 = performance.now()
    try {
      const h = await api<Health>('/api/health', { cache: 'no-store' })
      setHealth(h)
      setProbe({ ok: h.status === 'ok', ms: Math.round(performance.now() - t0), at: Date.now() })
      setLastUp(Date.now())
    } catch (e) {
      setProbe({ ok: false, ms: Math.round(performance.now() - t0), at: Date.now(), error: (e as Error).message })
    }
  }, [])

  useEffect(() => {
    const first = window.setTimeout(check, 0)
    timer.current = window.setInterval(check, POLL_MS)
    return () => {
      window.clearTimeout(first)
      window.clearInterval(timer.current)
    }
  }, [check])

  const testMcp = async () => {
    const key = savedKey()
    if (!key) {
      setMcp('Create an API key first (API keys tab): the MCP endpoint needs one.')
      return
    }
    setMcp('Connecting…')
    try {
      const r = await mcpHandshake(key)
      setMcp(r.ok ? `MCP session initialised: ${r.server ?? 'server'} ${r.version ?? ''} answered over streamable HTTP.` : `MCP endpoint answered ${r.status}: ${r.detail ?? ''}`)
    } catch (e) {
      setMcp(`MCP endpoint unreachable: ${(e as Error).message}`)
    }
  }

  const up = probe?.ok ?? false
  const tools = health?.mcp.tools ?? []
  const auto = tools.filter((t) => t.startsWith('auto_'))
  const classic = tools.filter((t) => !t.startsWith('auto_'))
  const mcpUrl = `${origin()}/mcp`

  return (
    <div className="svc-page">
      <div className={`status-banner ${probe === null ? 'pending' : up ? 'up' : 'down'}`} role="status" aria-live="polite">
        <span className="beacon" aria-hidden="true" />
        <div>
          <div className="status-title">
            {probe === null ? 'Checking the service…' : up ? 'All systems operational' : 'Service unreachable'}
          </div>
          <div className="status-sub">
            {probe === null
              ? 'Contacting /api/health'
              : up
                ? `REST API and MCP endpoint are up · health check ${probe.ms} ms · refreshed every ${POLL_MS / 1000}s`
                : `Last error: ${probe.error ?? 'unknown'} · last seen up ${lastUp ? ago(lastUp / 1000) : 'never'}`}
          </div>
        </div>
        <button type="button" className="copy-btn" onClick={check} aria-label="Check now">
          <ArrowClockwise size={13} /> Check now
        </button>
      </div>

      <div className="svc-grid-4 mt">
        <div className="stat">
          <div className="num">{health ? health.version : '–'}</div>
          <p>engine and plugin version</p>
        </div>
        <div className="stat">
          <div className="num">{health ? duration(health.uptime_s) : '–'}</div>
          <p>uptime of this server process</p>
        </div>
        <div className="stat">
          <div className="num">
            {health ? health.mcp.tool_count : '–'}
            <small>tools</small>
          </div>
          <p>MCP tools served at /mcp ({auto.length} AutoM2M)</p>
        </div>
        <div className="stat">
          <div className="num">
            {health ? health.metrics.tool_calls_total : '–'}
            <small>calls</small>
          </div>
          <p>MCP tool calls since start · last {ago(health?.metrics.last_mcp_call)}</p>
        </div>
      </div>

      <div className="grid-2 mt">
        <div className="card">
          <h3 className="subhead">Components</h3>
          <ul className="comp-list">
            <li>
              <span className={`dot ${up ? 'ok' : 'bad'}`} /> <b>Web app</b>
              <span className="muted"> served from this origin</span>
            </li>
            <li>
              <span className={`dot ${up ? 'ok' : 'bad'}`} /> <b>REST API</b> <code>/api</code>
              <span className="muted"> · {health?.metrics.api_requests ?? 0} requests</span>
            </li>
            <li>
              <span className={`dot ${up && health ? 'ok' : 'bad'}`} /> <b>MCP endpoint</b> <code>/mcp</code>
              <span className="muted"> · {health?.mcp.transport ?? 'streamable-http'} · {health?.metrics.mcp_requests ?? 0} requests</span>
            </li>
            <li>
              <span className={`dot ${health ? 'ok' : 'bad'}`} /> <b>LLM</b>
              <span className="muted">
                {' '}
                host mode (your Claude does the LLM work) · unattended solve:{' '}
                {health?.llm.solve_backend ? health.llm.solve_backend : 'not configured'}
              </span>
            </li>
            <li>
              <span className={`dot ${health?.execution.enabled ? 'ok' : 'warn'}`} /> <b>Code execution</b>
              <span className="muted">
                {' '}
                {health
                  ? health.execution.enabled
                    ? `${health.execution.sandbox} sandbox${health.execution.isolated ? ' (isolated, no network)' : ' (not isolated: trusted deployment)'}`
                    : 'disabled: values whose validators run code cannot be checked here'
                  : '–'}
              </span>
            </li>
            <li>
              <span className="dot ok" /> <b>API keys</b>
              <span className="muted">
                {' '}
                {health?.keys.active ?? 0} active · {health?.metrics.unauthorized ?? 0} rejected requests
              </span>
            </li>
          </ul>
          {health && !health.execution.enabled && <p className="caption">{health.execution.reason}</p>}
          {health?.metrics.last_error && (
            <p className="caption">
              <b>Last server error</b> ({ago(health.metrics.last_error.t)}): {health.metrics.last_error.message}
            </p>
          )}
          <div className="btn-row" style={{ marginTop: 18 }}>
            <button type="button" className="btn" onClick={testMcp}>
              <Plugs size={18} /> Test MCP handshake
            </button>
          </div>
          {mcp && <p className="caption">{mcp}</p>}
        </div>
        <div className="card">
          <h3 className="subhead">Connect Claude Code</h3>
          <Snippet
            title="Remote MCP server"
            text={`claude mcp add --transport http agenthot ${mcpUrl} \\\n  --header "Authorization: Bearer <your API key>"`}
          />
          <p className="caption">
            Then ask Claude to use the <code>auto_*</code> tools, e.g. <i>“set this file as the AutoM2M task, build and check a team, fill its values until φ holds”</i>. Add{' '}
            <code>--header "X-AgentHOT-Project: name"</code> to keep projects apart.
          </p>
        </div>
      </div>

      <div className="card mt">
        <h3 className="subhead">Tools on the MCP endpoint</h3>
        <div className="tool-cols">
          <div>
            <div className="lbl">AutoM2M ({auto.length})</div>
            <div className="chips">
              {auto.map((t) => (
                <span key={t} className="pill acc">
                  {t}
                  {health?.metrics.tool_calls[t] ? <b className="cnt">{health.metrics.tool_calls[t]}</b> : null}
                </span>
              ))}
            </div>
          </div>
          <div>
            <div className="lbl">AgentHOT ({classic.length})</div>
            <div className="chips">
              {classic.map((t) => (
                <span key={t} className="pill">
                  {t}
                  {health?.metrics.tool_calls[t] ? <b className="cnt">{health.metrics.tool_calls[t]}</b> : null}
                </span>
              ))}
            </div>
          </div>
        </div>
        {!health && <p className="caption">Tool list unavailable while the service is down.</p>}
      </div>
    </div>
  )
}
