import { EXPERIMENT_STATUS, NODE_TYPES, SEVERITY } from '../../utils/constants'

/** A status pill: pale tint of its own hue, 11px text, never above a heading. */
export function Badge({ className = '', children, title }) {
  return (
    <span title={title} className={`inline-flex items-center gap-1 whitespace-nowrap rounded-full px-2 py-0.5 text-[11px] font-medium leading-4 ${className || 'bg-neutral-bg text-neutral-ink'}`}>
      {children}
    </span>
  )
}

export function SeverityBadge({ severity }) {
  const s = SEVERITY[severity] || SEVERITY.info
  return <Badge className={s.cls}>{s.label}</Badge>
}

export function StatusBadge({ status }) {
  const s = EXPERIMENT_STATUS[status] || { label: status, cls: '' }
  return <Badge className={s.cls}>{s.label}</Badge>
}

const EVIDENCE = {
  confirmed: { label: 'Confirmed', cls: 'bg-positive-bg text-positive-ink', title: 'Directly visible in source code' },
  inferred: { label: 'Inferred', cls: 'bg-warning-bg text-warning-ink', title: 'Relies on a static-analysis heuristic' },
  unknown: { label: 'Unknown', cls: 'bg-neutral-bg text-neutral-ink', title: 'Not determinable statically' },
}
export function EvidenceBadge({ classification }) {
  const e = EVIDENCE[classification] || EVIDENCE.unknown
  return <Badge className={e.cls} title={e.title}>{e.label}</Badge>
}

export function TypeBadge({ type }) {
  const t = NODE_TYPES[type]
  if (!t) return <Badge>{type}</Badge>
  const Icon = t.icon
  return (
    <Badge className="bg-neutral-bg text-neutral-ink" title={t.label}>
      <Icon className="h-3 w-3" style={{ color: t.color }} />
      <span>{t.label}</span>
    </Badge>
  )
}

export function CertaintyBadge({ certainty }) {
  if (certainty === 'definite') return <Badge className="bg-negative-bg text-negative-ink" title="Reached without crossing a handled call site">Definite</Badge>
  if (certainty === 'possible') return <Badge className="bg-warning-bg text-warning-ink" title="Only reached through call sites that handle the error">Possible</Badge>
  return <Badge>Unaffected</Badge>
}
