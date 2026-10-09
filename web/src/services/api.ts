// Same-origin calls to the agentm2m service (vite proxies /api and /mcp in dev).

export type Health = {
  status: string
  service: string
  version: string
  started_at: number
  uptime_s: number
  mcp: { endpoint: string; transport: string; auth: string; tools: string[]; tool_count: number }
  llm: { mode: string; solve_backend: string | null }
  execution: { sandbox: string; isolated: boolean; enabled: boolean; reason: string }
  keys: { active: number; total: number }
  metrics: {
    requests: number
    api_requests: number
    mcp_requests: number
    unauthorized: number
    tool_calls: Record<string, number>
    tool_calls_total: number
    last_mcp_call: number | null
    last_error: { t: number; where: string; message: string } | null
  }
}

export type Diagnostic = { cond: string; element: string; message: string }
export type CheckResult = {
  admitted: boolean
  violated: string[]
  diagnostics: Diagnostic[]
  warnings: Diagnostic[]
  report: string
}

export type KeyRecord = {
  id: string
  prefix: string
  label: string
  email: string
  tier: string
  created: number
  last_used: number | null
  request_count: number
  revoked: boolean
}

export type Tier = {
  id: string
  name: string
  price: string
  period: string
  available: boolean
  summary: string
  features: string[]
}

export class ApiError extends Error {
  status: number
  constructor(status: number, message: string) {
    super(message)
    this.status = status
  }
}

export async function api<T>(path: string, init: RequestInit & { key?: string | null } = {}): Promise<T> {
  const headers = new Headers(init.headers)
  if (init.body && !headers.has('content-type')) headers.set('content-type', 'application/json')
  if (init.key) headers.set('authorization', `Bearer ${init.key}`)
  const res = await fetch(path, { ...init, headers })
  const text = await res.text()
  let data: unknown = null
  try {
    data = text ? JSON.parse(text) : null
  } catch {
    data = text
  }
  if (!res.ok) {
    const detail = (data && typeof data === 'object' && 'detail' in data ? (data as { detail: unknown }).detail : text) as string
    throw new ApiError(res.status, typeof detail === 'string' ? detail : JSON.stringify(detail))
  }
  return data as T
}

/** Initialize an MCP session over streamable HTTP: proves the MCP endpoint answers, not just REST. */
export async function mcpHandshake(key: string): Promise<{ ok: boolean; server?: string; version?: string; status: number; detail?: string }> {
  const res = await fetch('/mcp', {
    method: 'POST',
    headers: {
      'content-type': 'application/json',
      accept: 'application/json, text/event-stream',
      authorization: `Bearer ${key}`,
    },
    body: JSON.stringify({
      jsonrpc: '2.0',
      id: 1,
      method: 'initialize',
      params: { protocolVersion: '2025-06-18', capabilities: {}, clientInfo: { name: 'autom2m-web', version: '1' } },
    }),
  })
  const text = await res.text()
  if (!res.ok) return { ok: false, status: res.status, detail: text.slice(0, 300) }
  const m = text.match(/"serverInfo"\s*:\s*\{[^}]*"name"\s*:\s*"([^"]+)"[^}]*"version"\s*:\s*"([^"]*)"/)
  return { ok: true, status: res.status, server: m?.[1], version: m?.[2] }
}

const KEY_STORE = 'autom2m-api-key'

export function savedKey(): string | null {
  try {
    return localStorage.getItem(KEY_STORE)
  } catch {
    return null
  }
}

export function saveKey(key: string | null): void {
  try {
    if (key) localStorage.setItem(KEY_STORE, key)
    else localStorage.removeItem(KEY_STORE)
  } catch {
    /* storage may be unavailable */
  }
}

export function origin(): string {
  return window.location.origin
}

export function ago(t: number | null | undefined): string {
  if (!t) return 'never'
  const s = Math.max(0, Date.now() / 1000 - t)
  if (s < 60) return `${Math.round(s)}s ago`
  if (s < 3600) return `${Math.round(s / 60)}m ago`
  if (s < 86400) return `${Math.round(s / 3600)}h ago`
  return `${Math.round(s / 86400)}d ago`
}

export function duration(s: number): string {
  const d = Math.floor(s / 86400)
  const h = Math.floor((s % 86400) / 3600)
  const m = Math.floor((s % 3600) / 60)
  const sec = Math.floor(s % 60)
  if (d) return `${d}d ${h}h`
  if (h) return `${h}h ${m}m`
  if (m) return `${m}m ${sec}s`
  return `${sec}s`
}
