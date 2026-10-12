import { useEffect, useRef, useState } from 'react'
import { MarkerType, type Edge, type Node, type NodeTypes } from '@xyflow/react'
import { FeatNode, NoteNode, type FeatData } from './nodes'
import { StaticFlow, type FlowEdgeData } from './shared'
import { useInView, useNarrow, useReducedMotion } from './hooks'

// Feature-level data-flow graphs for W4 (two-sided anchoring), as in the paper's Fig. 4.
// "p": production edge (expression or footprint); "c": check edge (validator read).
type G = {
  nodes: { id: string; x: number; y: number; data: FeatData }[]
  edges: { id: string; s: string; t: string; sh: string; th: string; why: string }[]
  layers: string[][] // BFS layers of edge ids out of the goal's data
  verdict: { ok: boolean; text: string }
  note?: { x: number; y: number; text: string }
}

const graphs: Record<'a' | 'b', G> = {
  a: {
    nodes: [
      { id: 'ex', x: 0, y: 0, data: { label: 'Example.call/expected', role: 'goal', sub: 'anchor features' } },
      { id: 'doc', x: 0, y: 130, data: { label: 'Method.docstring' } },
      { id: 'tc', x: 340, y: 0, data: { label: 'TestCase.code', role: 'beh', sub: 'test_valid' } },
      { id: 'con', x: 340, y: 130, data: { label: 'MethodDesign.contract' } },
      { id: 'body', x: 680, y: 65, data: { label: 'MethodImpl.body', role: 'beh', sub: 'passes_tests' } },
    ],
    edges: [
      { id: 'ex-tc', s: 'ex', t: 'tc', sh: 's-r', th: 't-l', why: 'p, c' },
      { id: 'doc-tc', s: 'doc', t: 'tc', sh: 's-r', th: 't-l', why: 'p' },
      { id: 'doc-con', s: 'doc', t: 'con', sh: 's-r', th: 't-l', why: 'p' },
      { id: 'con-body', s: 'con', t: 'body', sh: 's-r', th: 't-l', why: 'p' },
      { id: 'tc-body', s: 'tc', t: 'body', sh: 's-r', th: 't-l', why: 'p, c' },
    ],
    layers: [['ex-tc'], ['tc-body']],
    verdict: { ok: true, text: 'W4 passes: Example reaches TestCase.code by a production and a check edge' },
  },
  b: {
    nodes: [
      { id: 'ex', x: 0, y: 0, data: { label: 'Example.call/expected', role: 'goal', sub: 'anchor features' } },
      { id: 'sig', x: 0, y: 130, data: { label: 'Method.signature' } },
      { id: 'con', x: 340, y: 130, data: { label: 'MethodDesign.contract' } },
      { id: 'body', x: 680, y: 130, data: { label: 'MethodImpl.body' } },
      { id: 'ver', x: 680, y: 0, data: { label: 'TestRun.verdict', role: 'beh', sub: 'smoke_test' } },
    ],
    edges: [
      { id: 'sig-con', s: 'sig', t: 'con', sh: 's-r', th: 't-l', why: 'p' },
      { id: 'con-body', s: 'con', t: 'body', sh: 's-r', th: 't-l', why: 'p' },
      { id: 'body-ver', s: 'body', t: 'ver', sh: 's-t', th: 't-b', why: 'p, c' },
    ],
    layers: [],
    verdict: { ok: false, text: 'W4 fails: nothing reads an Example, although the team does test something' },
    note: { x: 290, y: 4, text: 'no edge leaves\nthe goal data' },
  },
}

const nodeTypes: NodeTypes = { feat: FeatNode, note: NoteNode }

// vertical layouts for phones: [x, y] per node, [sourceHandle, targetHandle] per edge
const narrowLayout: Record<'a' | 'b', { pos: Record<string, [number, number]>; h: Record<string, [string, string]>; note?: [number, number] }> = {
  a: {
    pos: { ex: [0, 0], doc: [200, 0], tc: [0, 170], con: [200, 170], body: [100, 340] },
    h: { 'ex-tc': ['s-b', 't-t'], 'doc-tc': ['s-b', 't-t'], 'doc-con': ['s-b', 't-t'], 'con-body': ['s-b', 't-t'], 'tc-body': ['s-b', 't-t'] },
  },
  b: {
    pos: { ex: [0, 0], sig: [200, 0], con: [200, 170], body: [200, 340], ver: [0, 340] },
    h: { 'sig-con': ['s-b', 't-t'], 'con-body': ['s-b', 't-t'], 'body-ver': ['s-l', 't-r'] },
    note: [0, 175],
  },
}

export default function W4Flow() {
  const [which, setWhich] = useState<'a' | 'b'>('a')
  const [rawTick, setTick] = useState(0)
  const reduce = useReducedMotion()
  const ref = useRef<HTMLDivElement>(null)
  const inView = useInView(ref)
  const narrow = useNarrow()
  const g = graphs[which]
  const nl = narrowLayout[which]
  const maxTick = g.layers.length + 1 // final tick shows the verdict

  // with reduced motion, show the final state straight away
  const tick = reduce ? maxTick : rawTick

  useEffect(() => {
    if (reduce || !inView) return
    const t = setTimeout(() => setTick((v) => (v >= maxTick ? 0 : v + 1)), tick >= maxTick ? 3600 : 1300)
    return () => clearTimeout(t)
  }, [tick, inView, reduce, maxTick])

  const litEdges = new Set(g.layers.slice(0, tick).flat())
  const litNodes = new Set<string>(['ct'])
  g.edges.forEach((e) => litEdges.has(e.id) && litNodes.add(e.t))
  const showVerdict = tick >= maxTick
  const frontier = new Set(g.layers[tick - 1] ?? [])

  const nodes: Node[] = g.nodes.map((n) => ({
    id: n.id,
    type: 'feat',
    position: narrow ? { x: nl.pos[n.id][0], y: nl.pos[n.id][1] } : { x: n.x, y: n.y },
    data: { ...n.data, lit: litNodes.has(n.id) },
  }))
  if (g.note && showVerdict) nodes.push({ id: 'note', type: 'note', position: narrow && nl.note ? { x: nl.note[0], y: nl.note[1] } : { x: g.note.x, y: g.note.y }, data: { text: g.note.text } })

  const edges: Edge<FlowEdgeData>[] = g.edges.map((e) => {
    const lit = litEdges.has(e.id)
    const moving = frontier.has(e.id) && !showVerdict
    return {
      id: e.id,
      source: e.s,
      target: e.t,
      sourceHandle: narrow ? nl.h[e.id][0] : e.sh,
      targetHandle: narrow ? nl.h[e.id][1] : e.th,
      type: 'flow',
      className: lit ? (moving ? 'is-active' : 'is-done') : '',
      markerEnd: { type: MarkerType.ArrowClosed, width: 15, height: 15, color: lit ? 'var(--accent)' : 'var(--line-2)' },
      data: { label: narrow ? '' : e.why, state: moving ? 'active' : lit ? 'done' : 'idle', shape: 'bezier', reduce },
    }
  })

  return (
    <div className="flow-card" ref={ref}>
      <div className="flow-toolbar">
        <div className="tabs" role="tablist" aria-label="Team">
          <button role="tab" className="tab" aria-selected={which === 'a'} onClick={() => { setWhich('a'); setTick(0) }}>
            (a) admitted DevTeam
          </button>
          <button role="tab" className="tab" aria-selected={which === 'b'} onClick={() => { setWhich('b'); setTick(0) }}>
            (b) first proposal
          </button>
        </div>
        <div className="flow-status" aria-live="polite">
          {showVerdict ? (
            <span className={`pill ${g.verdict.ok ? 'pos' : 'neg'}`}>{g.verdict.text}</span>
          ) : (
            <span className="mono small muted">following arrows out of Example.call/expected…</span>
          )}
        </div>
      </div>
      <div className="flow-shell short" style={narrow ? { height: 440 } : undefined}>
        <StaticFlow key={`${which}-${narrow ? 'n' : 'w'}`} nodes={nodes} edges={edges} nodeTypes={nodeTypes} padding={0.12} label={`W4 data-flow graph, ${which === 'a' ? 'admitted team' : 'first proposal'}`} />
      </div>
      <div className="legend">
        <span><i style={{ borderColor: 'var(--warn)', background: 'var(--warn-soft)' }} />the goal's data</span>
        <span><i style={{ borderColor: 'var(--pos)', background: 'var(--pos-soft)' }} />checked by a behavioural validator</span>
        <span><i style={{ borderColor: 'var(--accent)' }} />reached from the goal</span>
      </div>
      <p className="caption">
        <b>Two-sided anchoring, by hand.</b> Each arrow is a production edge (p: an expression or footprint reads the
        feature) or a check edge (c: a validator reads it). In (a) the anchor features reach <code>TestCase.code</code>{' '}
        both ways: the producer of the tests saw the examples, and <code>test_valid</code> tests against them. In (b) no
        edge leaves the goal data, so W4 fails although the team does test something. A path-only rule would accept a team
        whose tests never see an example; a one-sided rule would accept a producer that never sees the goal as long as its
        validator reads it. The mutation study shows both blind spots.
      </p>
    </div>
  )
}
