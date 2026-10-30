import { Handle, Position } from '@xyflow/react'
import { memo } from 'react'
import { NODE_TYPES } from '../../utils/constants'

/** A single component in the architecture graph. Dimming/highlight state is passed in `data`. */
function GraphNode({ data, selected }) {
  const t = NODE_TYPES[data.type] || NODE_TYPES.function
  const Icon = t.icon
  const tone =
    data.state === 'impact-definite'
      ? 'border-negative/70 bg-negative/10'
      : data.state === 'impact-possible'
        ? 'border-warning/60 bg-warning/10'
        : data.state === 'source'
          ? 'border-negative bg-negative/20'
          : data.state === 'upstream'
            ? 'border-accent/60 bg-accent/10'
            : data.state === 'downstream'
              ? 'border-muted/60 bg-raised'
              : data.state === 'match'
                ? 'border-accent bg-accent/10'
                : 'border-edge bg-surface'
  return (
    <div
      className={`flex h-[46px] w-[210px] items-center gap-2 rounded-card border px-2.5 transition-[border-color,background-color,opacity] duration-200 ${tone} ${selected ? 'outline outline-1 outline-accent' : ''} ${data.dimmed ? 'opacity-25' : 'opacity-100'}`}
      title={data.id}
    >
      <Handle type="target" position={Position.Left} className="!h-1.5 !w-1.5 !border-0 !bg-control" />
      <span className="flex h-7 w-7 shrink-0 items-center justify-center" style={{ color: t.color }}>
        <Icon className="h-4 w-4" />
      </span>
      <div className="min-w-0 leading-tight">
        <div className="truncate text-[12px] font-medium text-ink">{data.label}</div>
        <div className="truncate text-[11px] leading-tight text-faint">{data.sub}</div>
      </div>
      {data.criticality > 0 && (
        <span className="num ml-auto shrink-0 rounded bg-ground px-1 text-[11px] text-muted" title="Criticality score">
          {Math.round(data.criticality)}
        </span>
      )}
      <Handle type="source" position={Position.Right} className="!h-1.5 !w-1.5 !border-0 !bg-control" />
    </div>
  )
}

export default memo(GraphNode)
