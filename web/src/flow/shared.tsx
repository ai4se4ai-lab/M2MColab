import { useEffect, type ReactNode } from 'react'
import {
  BaseEdge,
  EdgeLabelRenderer,
  ReactFlow,
  ReactFlowProvider,
  getBezierPath,
  getSmoothStepPath,
  useReactFlow,
  type Edge,
  type EdgeProps,
  type EdgeTypes,
  type Node,
  type NodeTypes,
} from '@xyflow/react'

export type FlowEdgeData = {
  label?: string
  state?: 'idle' | 'active' | 'done' | 'fail'
  dashed?: boolean
  shape?: 'step' | 'bezier'
  labelX?: number
  labelY?: number
  reduce?: boolean
}

/** Edge that highlights when active and carries a moving token along its path. */
export function FlowEdge(props: EdgeProps<Edge<FlowEdgeData>>) {
  const { sourceX, sourceY, targetX, targetY, sourcePosition, targetPosition, data, markerEnd, id } = props
  const shape = data?.shape ?? 'step'
  const [path, lx, ly] =
    shape === 'bezier'
      ? getBezierPath({ sourceX, sourceY, targetX, targetY, sourcePosition, targetPosition })
      : getSmoothStepPath({ sourceX, sourceY, targetX, targetY, sourcePosition, targetPosition, borderRadius: 14, offset: 26 })
  const state = data?.state ?? 'idle'
  const moving = (state === 'active' || state === 'fail') && !data?.reduce
  return (
    <>
      <BaseEdge id={id} path={path} markerEnd={markerEnd} />
      {moving && (
        <circle r={4.5} className={state === 'fail' ? 'edge-dot fail' : 'edge-dot'}>
          <animateMotion dur="1.3s" repeatCount="indefinite" path={path} />
        </circle>
      )}
      {data?.label && (
        <EdgeLabelRenderer>
          <div
            className={`edge-label ${state === 'active' ? 'active' : state === 'fail' ? 'fail' : ''}`}
            style={{ transform: `translate(-50%, -50%) translate(${lx + (data.labelX ?? 0)}px, ${ly + (data.labelY ?? 0)}px)` }}
          >
            {data.label}
          </div>
        </EdgeLabelRenderer>
      )}
    </>
  )
}

const edgeTypes: EdgeTypes = { flow: FlowEdge }

function FitOnResize({ padding }: { padding: number }) {
  const { fitView } = useReactFlow()
  useEffect(() => {
    const fit = () => fitView({ padding, duration: 0 })
    const t = setTimeout(fit, 30)
    window.addEventListener('resize', fit)
    return () => {
      clearTimeout(t)
      window.removeEventListener('resize', fit)
    }
  }, [fitView, padding])
  return null
}

/** A static, non-interactive React Flow canvas: the diagram is driven by our own state, not by dragging. */
export function StaticFlow({
  nodes,
  edges,
  nodeTypes,
  padding = 0.08,
  label,
  children,
}: {
  nodes: Node[]
  edges: Edge[]
  nodeTypes: NodeTypes
  padding?: number
  label: string
  children?: ReactNode
}) {
  return (
    <ReactFlowProvider>
      <div role="img" aria-label={label} style={{ width: '100%', height: '100%' }}>
        <ReactFlow
          nodes={nodes}
          edges={edges}
          nodeTypes={nodeTypes}
          edgeTypes={edgeTypes}
          fitView
          fitViewOptions={{ padding }}
          nodesDraggable={false}
          nodesConnectable={false}
          elementsSelectable={false}
          nodesFocusable={false}
          edgesFocusable={false}
          zoomOnScroll={false}
          zoomOnPinch={false}
          zoomOnDoubleClick={false}
          panOnDrag={false}
          panOnScroll={false}
          preventScrolling={false}
          minZoom={0.2}
        >
          <FitOnResize padding={padding} />
          {children}
        </ReactFlow>
      </div>
    </ReactFlowProvider>
  )
}
