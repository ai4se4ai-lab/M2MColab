import { useEffect, useMemo, useRef, useState } from 'react'
import { MarkerType, type Edge, type Node, type NodeTypes } from '@xyflow/react'
import { ArrowLeft, ArrowRight, Pause, Play } from '@phosphor-icons/react'
import { PhiNode, ViewNode, type PhiData, type ViewData, type ViewObj } from './nodes'
import { StaticFlow, type FlowEdgeData } from './shared'
import { useInView, useNarrow, useReducedMotion } from './hooks'

// The admitted typed DevTeam of the paper's running example (TaskBoard;
// teams/devteam_admitted_g2.json) running on AgentHOT.
type Phase = {
  label: string
  view?: string
  edges: string[]
  objs: Record<string, ViewObj[]>
  phi: PhiData['clauses']
  rule: string[]
  text: string
}

const goal: ViewObj[] = [
  { id: 'add_task', state: 'goal' },
  { id: 'mark_done', state: 'goal' },
  { id: 'export_csv', state: 'goal' },
  { id: 'E1.1', state: 'goal' },
  { id: 'E2.1 E2.2', state: 'goal' },
]
const ok = (...ids: string[]): ViewObj[] => ids.map((id) => ({ id, state: 'ok' }))
const M = ['add_task', 'mark_done', 'export_csv']
const idle: PhiData['clauses'] = [
  { k: 'cover(G)', v: 'idle' },
  { k: 'valid', v: 'idle' },
  { k: 'fresh', v: 'idle' },
  { k: 'noEsc', v: 'idle' },
]
const allOk: PhiData['clauses'] = idle.map((c) => ({ ...c, v: 'ok' }))
const failing: PhiData['clauses'] = [
  { k: 'cover(G)', v: 'fail' },
  { k: 'valid', v: 'ok' },
  { k: 'fresh', v: 'fail' },
  { k: 'noEsc', v: 'fail' },
]
const escalated: ViewObj[] = [{ id: 'add_task', state: 'ok' }, { id: 'mark_done', state: 'stale' }, { id: 'export_csv', state: 'ok' }]
const full = { Goal: goal, Design: ok(...M), Test: ok(...M), Code: ok(...M) }

const phases: Phase[] = [
  {
    label: 'Lift',
    view: 'Goal',
    edges: [],
    objs: { Goal: goal, Design: [], Test: [], Code: [] },
    phi: idle,
    rule: ['-- Goal view MM0, filled by Lift', 'Task { name, description, outline; methods[1-*], examples[0-*] }', 'Method { name, signature, docstring; examples[0-*] : Example }', 'Example { call, expected }   -- doctests moved out of docstrings'],
    text: 'The class skeleton is lifted deterministically into a goal model: one Task, three Methods and their Examples. A rule that reads a docstring does not see the examples; a footprint must name them.',
  },
  {
    label: 'Method2Design',
    view: 'Design',
    edges: ['Goal-Design'],
    objs: { Goal: goal, Design: ok(...M), Test: [], Code: [] },
    phi: idle,
    rule: ['rule Method2Design {                       -- Goal -> Design (Architect)', '  from m : Goal!Method', '  to   d : Design!MethodDesign ( name <- m.name, method <- m,', "       contract <- @llm('State a contract', Sequence{m.name, m.signature, m.docstring})", '       @check nonempty(contract) ) }'],
    text: 'The engine, not an LLM, decides which objects exist: one MethodDesign per Method. The Architect\'s LLM writes only the contract, from its declared footprint.',
  },
  {
    label: 'Method2Test',
    view: 'Test',
    edges: ['Goal-Test'],
    objs: { Goal: goal, Design: ok(...M), Test: ok(...M), Code: [] },
    phi: idle,
    rule: ['rule Method2Test {                         -- Goal -> Test (Tester, exec)', '  from m : Goal!Method', '  to   t : Test!TestCase ( name <- m.name, method <- m,', "       code <- @llm('Write unit tests', Sequence{m.signature, m.docstring,", '                    m.examples.call, m.examples.expected})', '       @check test_valid(code, m) ) }   -- runs, fails on a stub, asserts every example'],
    text: 'A test case exists for every method by construction, so no LLM can forget to test mark_done. The footprint and the validator both read the examples: that is what W4 calls anchoring.',
  },
  {
    label: 'Design2Impl',
    view: 'Code',
    edges: ['Design-Code', 'Test-Code'],
    objs: { Goal: goal, Design: ok(...M), Test: ok(...M), Code: escalated },
    phi: failing,
    rule: ['rule Design2Impl {                         -- Design, Test -> Code (Developer, exec)', '  from d : Design!MethodDesign, t : Test!TestCase (t.method = d.method)   -- join', '  to   i : Code!MethodImpl ( name <- d.name, method <- d.method,', "       body <- @llm('Implement the method', Sequence{d.method.signature, d.contract, t.code})", '       @check compiles(body) and passes_tests(body, t.code) ) }'],
    text: 'mark_done escalates after k = 3 samples fail passes_tests. The escalation is a recorded fault, not a hand-over to a person: noEsc and cover(G) fail, so φ is false.',
  },
  {
    label: 'attribute',
    view: 'Test',
    edges: ['Test-Code'],
    objs: { Goal: goal, Design: ok(...M), Test: [{ id: 'add_task', state: 'ok' }, { id: 'mark_done', state: 'stale' }, { id: 'export_csv', state: 'ok' }], Code: escalated },
    phi: failing,
    rule: ['lookup:   (impl mark_done, body), owner Developer', 'replays on the same footprint:            fail', 'widen by 1, 2 references:                 fail', 're-sample upstream t.code:                pass  -> UPSTREAM (Tester)', 'recurse:  widen Method2Test by 2 references (m.task.examples.*): pass -> FOOTPRINT'],
    text: 'Locating the symptom is a lookup. A bounded replay classifies it: the first test called mark_done(1) on a fresh board, without the add_task that E2.1 relies on. A footprint fault of Method2Test.',
  },
  {
    label: 'repair in place',
    view: 'Code',
    edges: ['Goal-Test', 'Test-Code'],
    objs: { Goal: goal, Design: ok(...M), Test: [{ id: 'add_task', state: 'ok' }, { id: 'mark_done', state: 'stale' }, { id: 'export_csv', state: 'ok' }], Code: escalated },
    phi: failing,
    rule: ['Δ: Method2Test.code footprint += m.task.examples.call, m.task.examples.expected', 'Admit(Θ ⊕ Δ) = ∅  -> applied in place', 'stale:  test(mark_done), impl(mark_done)', 'kept:   every other accepted value'],
    text: 'The mechanical delta is checked by the same checker, then applied in place. Only the test and the implementation of mark_done lose their stamps and are re-sampled.',
  },
  {
    label: 'evaluate φ',
    view: 'phi',
    edges: ['Code-phi'],
    objs: full,
    phi: allOk,
    rule: ['φ = cover(G) ∧ valid ∧ fresh ∧ noEsc', '-- cover(G): every Example and Method has a validated descendant', '-- fresh:    every stamp equals #(footprint ∪ validator reads)'],
    text: 'The engine decides that the team is done. A Tester that declares success early cannot end the run while the test for E2.2 fails.',
  },
]

const views: Record<string, Omit<ViewData, 'objs'> & { x: number; y: number }> = {
  Goal: { name: 'Goal', owner: 'LIFT · MM0', classes: 'Task, Method, Example', x: 0, y: 120 },
  Design: { name: 'Design', owner: 'ARCHITECT', classes: 'MethodDesign', x: 330, y: 0 },
  Test: { name: 'Test', owner: 'TESTER · exec', classes: 'TestCase', x: 330, y: 250 },
  Code: { name: 'Code', owner: 'DEVELOPER · exec', classes: 'MethodImpl', x: 660, y: 120 },
}
const edgeDefs = [
  { id: 'Goal-Design', s: 'Goal', t: 'Design', sh: 's-r', th: 't-l', label: 'Method2Design' },
  { id: 'Goal-Test', s: 'Goal', t: 'Test', sh: 's-r', th: 't-l', label: 'Method2Test' },
  { id: 'Design-Code', s: 'Design', t: 'Code', sh: 's-r', th: 't-l', label: 'Design2Impl' },
  { id: 'Test-Code', s: 'Test', t: 'Code', sh: 's-r', th: 't-l', label: 't.code' },
  { id: 'Code-phi', s: 'Code', t: 'phi', sh: 's-r', th: 't-l', label: 'evaluate' },
]
const nodeTypes: NodeTypes = { view: ViewNode, phi: PhiNode }
// vertical layout for phones
const narrowPos: Record<string, [number, number]> = { Goal: [120, 0], Design: [0, 200], Test: [240, 200], Code: [120, 400], phi: [130, 600] }

export default function RuntimeFlow() {
  const [ix, setIx] = useState(0)
  const reduce = useReducedMotion()
  const [playing, setPlaying] = useState(!reduce)
  const ref = useRef<HTMLDivElement>(null)
  const inView = useInView(ref)
  const narrow = useNarrow()
  const p = phases[ix]

  useEffect(() => {
    if (!playing || !inView || reduce) return
    const t = setTimeout(() => setIx((i) => (i + 1) % phases.length), ix === phases.length - 1 ? 4000 : 3000)
    return () => clearTimeout(t)
  }, [playing, inView, reduce, ix])

  const nodes: Node[] = useMemo(
    () => [
      ...Object.entries(views).map(([id, v]) => ({
        id,
        type: 'view',
        position: narrow ? { x: narrowPos[id][0], y: narrowPos[id][1] } : { x: v.x, y: v.y },
        data: { ...v, objs: p.objs[id] ?? [], active: p.view === id },
      })),
      { id: 'phi', type: 'phi', position: narrow ? { x: narrowPos.phi[0], y: narrowPos.phi[1] } : { x: 980, y: 104 }, data: { clauses: p.phi, active: p.view === 'phi' } },
    ],
    [p, narrow],
  )
  const edges: Edge<FlowEdgeData>[] = useMemo(
    () =>
      edgeDefs.map((e) => {
        const active = p.edges.includes(e.id)
        return {
          id: e.id,
          source: e.s,
          target: e.t,
          sourceHandle: narrow ? 's-b' : e.sh,
          targetHandle: narrow ? 't-t' : e.th,
          type: 'flow',
          className: active ? 'is-active' : '',
          markerEnd: { type: MarkerType.ArrowClosed, width: 16, height: 16, color: active ? 'var(--accent)' : 'var(--line-2)' },
          data: { label: e.label, state: active ? 'active' : 'idle', shape: 'bezier', reduce },
        }
      }),
    [p, reduce, narrow],
  )

  return (
    <div className="flow-card" ref={ref}>
      <div className="flow-toolbar">
        <div className="flow-status" aria-live="polite">
          AgentHOT runtime · <b>{p.label}</b>
        </div>
        <div className="ctrl">
          <button className="icon-btn" onClick={() => { setPlaying(false); setIx((ix - 1 + phases.length) % phases.length) }} aria-label="Previous phase">
            <ArrowLeft size={16} />
          </button>
          <button className="icon-btn" onClick={() => setPlaying((v) => !v)} aria-label={playing ? 'Pause animation' : 'Play animation'}>
            {playing ? <Pause size={16} weight="fill" /> : <Play size={16} weight="fill" />}
            {playing ? 'pause' : 'play'}
          </button>
          <button className="icon-btn" onClick={() => { setPlaying(false); setIx((ix + 1) % phases.length) }} aria-label="Next phase">
            <ArrowRight size={16} />
          </button>
        </div>
      </div>
      <div className="flow-shell short" style={{ height: narrow ? 560 : 'clamp(260px, 34vw, 360px)' }}>
        <StaticFlow key={narrow ? 'n' : 'w'} nodes={nodes} edges={edges} nodeTypes={nodeTypes} padding={0.05} label={`DevTeam running on AgentHOT, phase ${p.label}`} />
      </div>
      <div className="grid-2 wide-left mt-s" style={{ alignItems: 'start' }}>
        <div className="code" style={{ minHeight: 110 }}>
          {p.rule.map((l, i) => (
            <div key={`${ix}-${i}`} className={l.startsWith('--') ? 'c' : ''}>
              {l}
            </div>
          ))}
        </div>
        <p className="caption" style={{ marginTop: 0 }}>
          <b>{ix + 1} / {phases.length}.</b> {p.text}
        </p>
      </div>
      <div className="legend">
        <span><i style={{ borderColor: 'var(--warn)' }} />goal data</span>
        <span><i style={{ borderColor: 'var(--pos)', background: 'var(--pos-soft)' }} />accepted value, stamped</span>
        <span><i style={{ borderColor: 'var(--warn)', background: 'var(--warn-soft)' }} />escalated or stale: re-sampled</span>
      </div>
    </div>
  )
}
