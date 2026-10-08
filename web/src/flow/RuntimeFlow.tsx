import { useEffect, useMemo, useRef, useState } from 'react'
import { MarkerType, type Edge, type Node, type NodeTypes } from '@xyflow/react'
import { ArrowLeft, ArrowRight, Pause, Play } from '@phosphor-icons/react'
import { PhiNode, ViewNode, type PhiData, type ViewData, type ViewObj } from './nodes'
import { StaticFlow, type FlowEdgeData } from './shared'
import { useInView, useNarrow, useReducedMotion } from './hooks'

// The admitted DevTeam (teams/devteam_admitted.json) running on the AgentM2M engine.
type Phase = {
  label: string
  view?: string
  edges: string[]
  objs: Record<string, ViewObj[]>
  phi: PhiData['clauses']
  rule: string[]
  text: string
}

const req = (s21: ViewObj['state'] = 'goal'): ViewObj[] => [
  { id: 'S1', state: 'goal' },
  { id: 'S1.1', state: 'goal' },
  { id: 'S2', state: 'goal' },
  { id: 'S2.1', state: s21 },
  { id: 'S3 draft', state: 'off' },
]
const ok = (...ids: string[]): ViewObj[] => ids.map((id) => ({ id, state: 'ok' }))
const idle: PhiData['clauses'] = [
  { k: 'cover(G)', v: 'idle' },
  { k: 'valid', v: 'idle' },
  { k: 'fresh', v: 'idle' },
  { k: 'noObl', v: 'idle' },
]
const allOk: PhiData['clauses'] = idle.map((c) => ({ ...c, v: 'ok' }))
const full = { Req: req(), Arch: ok('op S1', 'op S2'), Test: ok('ts S1', 'ts S2'), Code: ok('edit S1', 'edit S2') }

const phases: Phase[] = [
  {
    label: 'Lift',
    view: 'Req',
    edges: [],
    objs: { Req: req(), Arch: [], Test: [], Code: [] },
    phi: idle,
    rule: ["-- Req, the goal view (Analyst's form)", 'UserStory { id, title, status; criteria[1-*] : Criterion }', 'Criterion { id, text; story : UserStory }'],
    text: 'The task is lifted into the goal view. Two stories are accepted; S3 is still a draft. Each agent fills in its own form, a small metamodel, not free text.',
  },
  {
    label: 'Story2Operation',
    view: 'Arch',
    edges: ['Req-Arch'],
    objs: { Req: req(), Arch: ok('op S1', 'op S2'), Test: [], Code: [] },
    phi: idle,
    rule: ['rule Story2Operation {                      -- Req -> Arch', "  from s : Req!UserStory (s.status = 'accepted')   -- guard", '  to  op : Arch!Operation ( name <- s.title,', "      signature <- @llm(prompt, fp(s.title, s.criteria)) @check compiles ) }"],
    text: 'The engine, not an LLM, decides which objects exist: one Operation per accepted story, none for the draft S3. The LLM fills only the signature, seeing only its footprint, and the value is accepted when its validator passes.',
  },
  {
    label: 'Story2TestSuite',
    view: 'Test',
    edges: ['Req-Test'],
    objs: { Req: req(), Arch: ok('op S1', 'op S2'), Test: ok('ts S1', 'ts S2'), Code: [] },
    phi: idle,
    rule: ['rule Story2TestSuite {                      -- Req -> Test', "  from s : Req!UserStory (s.status = 'accepted')", '  to  ts : Test!TestSuite (', '      code <- @llm(prompt, fp(s.title, s.criteria)) @check test_valid(s) ) }'],
    text: 'A test suite exists for every accepted story by construction: no LLM can forget one. test_valid is behavioural: the tests must call the method and fail on a stub, so they actually test something.',
  },
  {
    label: 'Op2Edit',
    view: 'Code',
    edges: ['Arch-Code', 'Test-Code'],
    objs: { Req: req(), Arch: ok('op S1', 'op S2'), Test: ok('ts S1', 'ts S2'), Code: ok('edit S1', 'edit S2') },
    phi: idle,
    rule: ['rule Op2Edit {                       -- Arch, Test -> Code', '  from op : Arch!Operation, ts : Test!TestSuite', '  to  e : Code!CodeEdit (', '      body <- @llm(prompt, fp(op.signature, op.story.criteria))', '      @check passes_tests(op.story, ts.code) ) }'],
    text: "The Developer's code must pass the Tester's suite, executed with the exec tool the Developer owns. Each accepted value is stored with a stamp: a digest of the footprint it was written from.",
  },
  {
    label: 'evaluate φ',
    view: 'phi',
    edges: ['Code-phi'],
    objs: full,
    phi: allOk,
    rule: ['φ = cover(G) ∧ valid ∧ fresh ∧ noObl', '-- cover(G): every accepted criterion has a validated descendant', '-- fresh:    every stamp matches its footprint'],
    text: 'The engine evaluates "done" on the models. An agent that declares success early cannot end the run.',
  },
  {
    label: 'edit S2.1',
    view: 'Req',
    edges: ['Req-Arch', 'Req-Test', 'Arch-Code'],
    objs: {
      Req: req('stale'),
      Arch: [{ id: 'op S1', state: 'ok' }, { id: 'op S2', state: 'stale' }],
      Test: [{ id: 'ts S1', state: 'ok' }, { id: 'ts S2', state: 'stale' }],
      Code: [{ id: 'edit S1', state: 'ok' }, { id: 'edit S2', state: 'stale' }],
    },
    phi: [
      { k: 'cover(G)', v: 'ok' },
      { k: 'valid', v: 'ok' },
      { k: 'fresh', v: 'fail' },
      { k: 'noObl', v: 'fail' },
    ],
    rule: ['stamp(op S2.signature)  != digest(S2.title, S2.criteria)   -> obligation', 'stamp(ts S2.code)       != digest(S2.title, S2.criteria)   -> obligation', 'stamp(edit S2.body)     != digest(op.signature, criteria) -> obligation'],
    text: 'The Analyst changes criterion S2.1. Comparing stamps, the engine finds exactly the three values whose footprint changed. S1 is left alone: none of its footprints changed.',
  },
  {
    label: 'redo obligations',
    view: 'phi',
    edges: ['Req-Arch', 'Req-Test', 'Arch-Code', 'Test-Code', 'Code-phi'],
    objs: full,
    phi: allOk,
    rule: ['re-sampled: op S2.signature, ts S2.code, edit S2.body', 'kept:       op S1, ts S1, edit S1'],
    text: 'Only the obligations are redone. φ holds again, and nothing else was regenerated.',
  },
]

const views: Record<string, Omit<ViewData, 'objs'> & { x: number; y: number }> = {
  Req: { name: 'Req', owner: 'ANALYST · GOAL', classes: 'UserStory, Criterion', x: 0, y: 120 },
  Arch: { name: 'Arch', owner: 'ARCHITECT', classes: 'Operation', x: 330, y: 0 },
  Test: { name: 'Test', owner: 'TESTER · exec', classes: 'TestSuite', x: 330, y: 250 },
  Code: { name: 'Code', owner: 'DEVELOPER · exec', classes: 'CodeEdit', x: 660, y: 120 },
}
const edgeDefs = [
  { id: 'Req-Arch', s: 'Req', t: 'Arch', sh: 's-r', th: 't-l', label: 'Story2Operation' },
  { id: 'Req-Test', s: 'Req', t: 'Test', sh: 's-r', th: 't-l', label: 'Story2TestSuite' },
  { id: 'Arch-Code', s: 'Arch', t: 'Code', sh: 's-r', th: 't-l', label: 'Op2Edit' },
  { id: 'Test-Code', s: 'Test', t: 'Code', sh: 's-r', th: 't-l', label: 'ts.code' },
  { id: 'Code-phi', s: 'Code', t: 'phi', sh: 's-r', th: 't-l', label: 'evaluate' },
]
const nodeTypes: NodeTypes = { view: ViewNode, phi: PhiNode }
// vertical layout for phones
const narrowPos: Record<string, [number, number]> = { Req: [120, 0], Arch: [0, 200], Test: [240, 200], Code: [120, 400], phi: [130, 600] }

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
          AgentM2M runtime · <b>{p.label}</b>
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
        <StaticFlow key={narrow ? 'n' : 'w'} nodes={nodes} edges={edges} nodeTypes={nodeTypes} padding={0.05} label={`DevTeam running on AgentM2M, phase ${p.label}`} />
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
        <span><i style={{ borderColor: 'var(--warn)', background: 'var(--warn-soft)' }} />stale stamp: obligation</span>
      </div>
    </div>
  )
}
