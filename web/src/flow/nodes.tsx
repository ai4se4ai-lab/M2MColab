import { Handle, Position, type Node, type NodeProps } from '@xyflow/react'

/** Every node exposes a source and a target handle on each side, so edges can be routed explicitly. */
export function Handles() {
  const sides = [
    ['t', Position.Top],
    ['r', Position.Right],
    ['b', Position.Bottom],
    ['l', Position.Left],
  ] as const
  return (
    <>
      {sides.map(([k, p]) => (
        <Handle key={`s${k}`} id={`s-${k}`} type="source" position={p} isConnectable={false} />
      ))}
      {sides.map(([k, p]) => (
        <Handle key={`t${k}`} id={`t-${k}`} type="target" position={p} isConnectable={false} />
      ))}
    </>
  )
}

export type StepState = 'idle' | 'active' | 'ok' | 'fail'
export type PipeData = {
  step?: string
  name: string
  desc?: string
  kind: 'llm' | 'eng' | 'mod' | 'raw'
  state?: StepState
  focus?: boolean
  verdict?: { text: string; ok: boolean }
  compact?: boolean
}

const kindLabel = { llm: 'LLM', eng: 'DETERMINISTIC', mod: 'ARTEFACT', raw: 'INPUT' }

export function PipeNode({ data }: NodeProps<Node<PipeData>>) {
  const st = data.state ?? 'idle'
  const cls = ['pnode', data.compact ? 'compact' : '', st !== 'idle' ? `st-${st}` : '', data.focus ? 'st-active' : '']
  return (
    <div className={cls.join(' ')}>
      <Handles />
      <div className="step">
        <span>{data.step ?? ''}</span>
        <span className={`kind ${data.kind}`}>{kindLabel[data.kind]}</span>
      </div>
      <div className="name">{data.name}</div>
      {data.desc && <div className="desc">{data.desc}</div>}
      {data.verdict && <div className={`verdict ${data.verdict.ok ? 'ok' : 'fail'}`}>{data.verdict.text}</div>}
    </div>
  )
}

export type ViewObj = { id: string; state?: 'ok' | 'stale' | 'off' | 'goal' | 'plain' }
export type ViewData = {
  name: string
  owner: string
  classes: string
  objs: ViewObj[]
  active?: boolean
}

export function ViewNode({ data }: NodeProps<Node<ViewData>>) {
  return (
    <div className={`vnode ${data.active ? 'active' : ''}`}>
      <Handles />
      <div className="hdr">
        <b>{data.name}</b>
        <span className="owner">{data.owner}</span>
      </div>
      <div className="cls">{data.classes}</div>
      <div className="objs">
        {data.objs.map((o) => (
          <span key={o.id} className={`obj ${o.state ?? ''}`}>
            {o.id}
          </span>
        ))}
      </div>
    </div>
  )
}

export type PhiData = { clauses: { k: string; v: 'ok' | 'fail' | 'idle' }[]; active?: boolean }
export function PhiNode({ data }: NodeProps<Node<PhiData>>) {
  const all = data.clauses.every((c) => c.v === 'ok')
  return (
    <div className={`vnode phi ${data.active ? 'active' : ''}`}>
      <Handles />
      <div className="hdr">
        <b>φ, decided by the engine</b>
      </div>
      <div style={{ marginTop: 6 }}>
        {data.clauses.map((c) => (
          <div key={c.k} className={`cl ${c.v === 'idle' ? '' : c.v}`}>
            <span>{c.k}</span>
            <span>{c.v === 'ok' ? 'holds' : c.v === 'fail' ? 'fails' : '...'}</span>
          </div>
        ))}
      </div>
      <div className={`cl ${all ? 'ok' : ''}`} style={{ borderTop: '1px solid var(--line)', marginTop: 6, paddingTop: 6, fontFamily: 'var(--mono)', fontSize: 12 }}>
        {all ? 'done' : 'not done'}
      </div>
    </div>
  )
}

export type FeatData = { label: string; role?: 'goal' | 'beh' | 'plain'; lit?: boolean; sub?: string }
export function FeatNode({ data }: NodeProps<Node<FeatData>>) {
  return (
    <div className={`fnode ${data.role ?? ''} ${data.lit ? 'lit' : ''}`}>
      <Handles />
      {data.label}
      {data.sub && <span className="sub">{data.sub}</span>}
    </div>
  )
}

export function NoteNode({ data }: NodeProps<Node<{ text: string }>>) {
  return (
    <div className="note-node">
      <Handles />
      {data.text}
    </div>
  )
}
