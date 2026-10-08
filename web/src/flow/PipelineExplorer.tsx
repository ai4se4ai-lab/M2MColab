import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { MarkerType, type Edge, type Node, type NodeTypes } from '@xyflow/react'
import { ArrowLeft, ArrowRight, Pause, Play, ArrowCounterClockwise } from '@phosphor-icons/react'
import { PipeNode, type PipeData, type StepState } from './nodes'
import { StaticFlow, type FlowEdgeData } from './shared'
import { useInView, useNarrow, useReducedMotion } from './hooks'
import { scenarios, type Line, type Scenario } from '../data/scenarios'

const nodeTypes: NodeTypes = { pipe: PipeNode }

const baseNodes: { id: string; x: number; y: number; data: PipeData }[] = [
  { id: 'task', x: 0, y: 60, data: { step: 'input', name: 'Task', desc: 'lifted into the goal view', kind: 'raw', compact: true } },
  { id: 'A', x: 175, y: 60, data: { step: 'A', name: 'Builder LLM', desc: 'proposes a typed team', kind: 'llm' } },
  { id: 'team', x: 405, y: 60, data: { step: 'Θ', name: 'Typed team', desc: 'agents, forms, rules, goal, φ', kind: 'mod' } },
  { id: 'B', x: 635, y: 60, data: { step: 'B', name: 'Checker W1-W6', desc: 'admits or rejects, no LLM', kind: 'eng' } },
  { id: 'D', x: 865, y: 60, data: { step: 'D', name: 'AgentM2M runtime', desc: 'compile rules, run to fixpoint', kind: 'eng' } },
  { id: 'done', x: 1095, y: 60, data: { step: 'φ', name: 'Done', desc: 'engine decides', kind: 'eng', compact: true } },
  { id: 'E', x: 865, y: 285, data: { step: 'E', name: 'Attribution', desc: 'trace lookup + replay', kind: 'eng' } },
  { id: 'F', x: 405, y: 285, data: { step: 'F', name: 'Repair', desc: 'mechanical, or builder delta', kind: 'llm' } },
]

const baseEdges: { id: string; s: string; t: string; sh: string; th: string; label?: string; lx?: number; ly?: number; dashed?: boolean }[] = [
  { id: 'task-A', s: 'task', t: 'A', sh: 's-r', th: 't-l' },
  { id: 'A-team', s: 'A', t: 'team', sh: 's-r', th: 't-l' },
  { id: 'team-B', s: 'team', t: 'B', sh: 's-r', th: 't-l' },
  { id: 'B-A', s: 'B', t: 'A', sh: 's-t', th: 't-t', label: 'C · diagnostics', dashed: true },
  { id: 'B-D', s: 'B', t: 'D', sh: 's-r', th: 't-l', label: 'admit', ly: -12 },
  { id: 'D-done', s: 'D', t: 'done', sh: 's-r', th: 't-l', label: 'φ holds', ly: -12 },
  { id: 'D-E', s: 'D', t: 'E', sh: 's-b', th: 't-t', label: 'φ fails', lx: 30 },
  { id: 'E-F', s: 'E', t: 'F', sh: 's-l', th: 't-r', label: 'fault report' },
  { id: 'F-B', s: 'F', t: 'B', sh: 's-t', th: 't-b', label: 're-check Θ ⊕ Δ', dashed: true },
]

// vertical layout for phones: the main path runs down the left, the repair loop on the right
const narrowPos: Record<string, [number, number]> = {
  task: [20, 0], A: [0, 140], team: [0, 280], B: [0, 420], D: [0, 560], done: [20, 700], F: [250, 280], E: [250, 560],
}
const narrowHandles: Record<string, [string, string, number?, number?]> = {
  'task-A': ['s-b', 't-t'], 'A-team': ['s-b', 't-t'], 'team-B': ['s-b', 't-t'],
  'B-A': ['s-l', 't-l', 4, 0], 'B-D': ['s-b', 't-t', 28, 0], 'D-done': ['s-b', 't-t', 34, 0],
  'D-E': ['s-r', 't-l', 0, -12], 'E-F': ['s-t', 't-b', 44, 0], 'F-B': ['s-l', 't-r', 0, 0],
}

function renderLine(l: Line, i: number) {
  if (typeof l === 'string') return <div key={i}>{l || ' '}</div>
  return (
    <div key={i} className={l[1]}>
      {l[0]}
    </div>
  )
}

export default function PipelineExplorer() {
  const [sid, setSid] = useState(0)
  const [ix, setIx] = useState(0)
  const reduce = useReducedMotion()
  const [playing, setPlaying] = useState(!reduce)
  const ref = useRef<HTMLDivElement>(null)
  const inView = useInView(ref)
  const narrow = useNarrow()
  const sc: Scenario = scenarios[sid]
  const step = sc.steps[ix]
  const last = sc.steps.length - 1

  const go = useCallback(
    (n: number, stop = true) => {
      setIx(Math.max(0, Math.min(last, n)))
      if (stop) setPlaying(false)
    },
    [last],
  )

  // autoplay: advance while visible; pause on the last step, then start again
  useEffect(() => {
    if (!playing || !inView || reduce) return
    const t = setTimeout(() => setIx((i) => (i >= last ? 0 : i + 1)), ix >= last ? 4200 : 3400)
    return () => clearTimeout(t)
  }, [playing, inView, reduce, ix, last])

  // keyboard stepping while the explorer is on screen
  useEffect(() => {
    if (!inView) return
    const on = (e: KeyboardEvent) => {
      const tag = (e.target as HTMLElement)?.tagName
      if (tag === 'INPUT' || tag === 'TEXTAREA') return
      if (e.key === 'ArrowRight') go(ix + 1)
      else if (e.key === 'ArrowLeft') go(ix - 1)
    }
    window.addEventListener('keydown', on)
    return () => window.removeEventListener('keydown', on)
  }, [inView, ix, go])

  const nodes: Node[] = useMemo(
    () =>
      baseNodes.map((n) => {
        const st: StepState = step.marks[n.id] ?? 'idle'
        const v = step.verdicts?.[n.id] ?? undefined
        return {
          id: n.id,
          type: 'pipe',
          position: narrow ? { x: narrowPos[n.id][0], y: narrowPos[n.id][1] } : { x: n.x, y: n.y },
          data: { ...n.data, state: st, focus: step.focus === n.id, verdict: v ?? undefined },
        }
      }),
    [step, narrow],
  )

  const edges: Edge<FlowEdgeData>[] = useMemo(
    () =>
      baseEdges.map((e) => {
        const active = step.edges.includes(e.id)
        const fail = step.failEdges?.includes(e.id)
        const state: FlowEdgeData['state'] = fail ? 'fail' : active ? 'active' : 'idle'
        const color = fail ? 'var(--neg)' : active ? 'var(--accent)' : 'var(--line-2)'
        const nh = narrowHandles[e.id]
        return {
          id: e.id,
          source: e.s,
          target: e.t,
          sourceHandle: narrow ? nh[0] : e.sh,
          targetHandle: narrow ? nh[1] : e.th,
          type: 'flow',
          className: [state !== 'idle' ? `is-${state}` : '', e.dashed ? 'is-dashed' : ''].join(' '),
          markerEnd: { type: MarkerType.ArrowClosed, width: 16, height: 16, color },
          data: { label: e.label, state, labelX: narrow ? nh[2] : e.lx, labelY: narrow ? nh[3] : e.ly, reduce },
        }
      }),
    [step, reduce, narrow],
  )

  return (
    <div ref={ref}>
      <div className="tabs" role="tablist" aria-label="Walkthrough scenario">
        {scenarios.map((s, i) => (
          <button
            key={s.id}
            role="tab"
            className="tab"
            aria-selected={i === sid}
            onClick={() => {
              setSid(i)
              setIx(0)
              setPlaying(!reduce)
            }}
          >
            {s.tab}
          </button>
        ))}
      </div>
      <div className="meta-line">
        {sc.meta.map(([k, v]) => (
          <span key={k}>
            <b>{k}</b>
            {v}
          </span>
        ))}
      </div>
      <div className="mini-stats">
        {sc.stats.map((s) => (
          <div className="mini" key={s.l}>
            <div className={`v ${s.tone ?? ''}`}>{s.v}</div>
            <div className="l">{s.l}</div>
          </div>
        ))}
      </div>

      <div className="flow-card mt-s">
        <div className="flow-toolbar">
          <div className="flow-status" aria-live="polite">
            step <b>{ix + 1}</b> / {sc.steps.length} · {step.short}
          </div>
          <div className="ctrl">
            <button className="icon-btn" onClick={() => go(ix - 1)} disabled={ix === 0} aria-label="Previous step">
              <ArrowLeft size={16} />
            </button>
            <button
              className="icon-btn"
              onClick={() => {
                if (ix >= last) setIx(0)
                setPlaying((p) => !p)
              }}
              aria-label={playing ? 'Pause animation' : 'Play animation'}
            >
              {playing ? <Pause size={16} weight="fill" /> : <Play size={16} weight="fill" />}
              {playing ? 'pause' : 'play'}
            </button>
            <button className="icon-btn" onClick={() => go(ix + 1)} disabled={ix === last} aria-label="Next step">
              <ArrowRight size={16} />
            </button>
            <button className="icon-btn" onClick={() => go(0)} aria-label="Restart">
              <ArrowCounterClockwise size={16} />
            </button>
          </div>
        </div>
        <div className="flow-shell" style={{ height: narrow ? 640 : 400 }}>
          <StaticFlow key={narrow ? 'n' : 'w'} nodes={nodes} edges={edges} nodeTypes={nodeTypes} padding={0.06} label={`AutoM2M pipeline, ${step.title}`} />
        </div>
        <div className="legend">
          <span><i style={{ borderColor: 'var(--llm)', background: 'var(--llm-soft)' }} />LLM proposes</span>
          <span><i style={{ borderColor: 'var(--pos)', background: 'var(--pos-soft)' }} />deterministic step decides</span>
          <span><i style={{ borderColor: 'var(--accent)' }} />current step</span>
          <span><i style={{ borderColor: 'var(--neg)' }} />rejected or failed</span>
        </div>
        <p className="caption">
          <b>Algorithm 1, step by step.</b> {sc.note} Use ← / → to step.
        </p>
      </div>

      <div className="explorer">
        <nav className="steps-list" aria-label="Steps">
          <div className="hd">{sc.steps.length} steps</div>
          {sc.steps.map((s, i) => (
            <button key={s.title} className="step-btn" aria-current={i === ix ? 'step' : undefined} onClick={() => go(i)}>
              <span className="ix">{String(i + 1).padStart(2, '0')}</span>
              <span className={`mk ${s.mark}`} aria-hidden="true" />
              <span>{s.title}</span>
            </button>
          ))}
        </nav>
        <div className="detail">
          <div className="nav-row">
            <button className="icon-btn" onClick={() => go(ix - 1)} disabled={ix === 0}>
              ← prev
            </button>
            <div className="counter">
              step {ix + 1} / {sc.steps.length}
            </div>
            <button className="icon-btn" onClick={() => go(ix + 1)} disabled={ix === last}>
              next →
            </button>
          </div>
          <div className="fade" key={`${sid}-${ix}`}>
            <h3>{step.title}</h3>
            <div className="badges">
              {step.badges.map((b) => (
                <span key={b.text} className={`pill ${b.tone}`}>
                  {b.text}
                </span>
              ))}
            </div>
            <div className="lbl">what happens</div>
            <p>{step.body}</p>
            <div className="lbl">{step.artefactLabel}</div>
            <div className="code">{step.artefact.map(renderLine)}</div>
          </div>
        </div>
      </div>
    </div>
  )
}
