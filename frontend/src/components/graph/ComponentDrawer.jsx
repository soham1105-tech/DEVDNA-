import { motion } from 'framer-motion'
import { ArrowDownRight, ArrowUpLeft, Crosshair, FileCode2, Radar, Zap } from 'lucide-react'
import { useState } from 'react'
import { Link } from 'react-router-dom'
import { useAsync } from '../../hooks/useAsync'
import { api } from '../../services/api'
import { EDGE_LABELS, NODE_TYPES } from '../../utils/constants'
import { shortId } from '../../utils/graph'
import { Badge, Button, Drawer, ErrorState, EvidenceBadge, SeverityBadge, Skeleton, TypeBadge } from '../ui'

const SCENARIO_FOR = { database: 'db_unavailable', external_api: 'external_api_unavailable', env_var: 'missing_env_var', worker: 'worker_unavailable' }
const TABS = ['Details', 'Relationships', 'Source', 'Findings']

export function EvidenceList({ evidence }) {
  if (!evidence?.length) return <p className="text-xs text-faint">No source evidence recorded.</p>
  return (
    <ul className="space-y-1.5">
      {evidence.map((ev, i) => (
        <li key={i} className="rounded-card border border-edge bg-ground px-2.5 py-1.5">
          <div className="num flex items-center gap-2 text-[11px] text-accent">
            <FileCode2 className="h-3 w-3" /> {ev.file}{ev.line ? `:${ev.line}` : ''}
          </div>
          {ev.snippet && <code className="num mt-1 block whitespace-pre-wrap break-all text-[11px] text-muted">{ev.snippet}</code>}
          {ev.detail && <div className="mt-0.5 text-[11px] text-faint">{ev.detail}</div>}
        </li>
      ))}
    </ul>
  )
}

function EdgeRow({ edge, otherId, direction, onSelectNode }) {
  const [open, setOpen] = useState(false)
  return (
    <li className="rounded-card border border-edge bg-ground">
      <div className="flex items-center gap-2 px-3 py-2">
        {direction === 'out' ? <ArrowDownRight className="h-3.5 w-3.5 text-faint" /> : <ArrowUpLeft className="h-3.5 w-3.5 text-faint" />}
        <span className="text-[11px] text-faint">{EDGE_LABELS[edge.type] || edge.type}</span>
        <button onClick={() => onSelectNode(otherId)} className="num min-w-0 flex-1 truncate text-left text-xs text-ink hover:text-accent">
          {shortId(otherId)}
        </button>
        <EvidenceBadge classification={edge.classification} />
        <button onClick={() => setOpen((o) => !o)} className="text-[11px] text-accent hover:underline" aria-expanded={open}>
          {open ? 'Hide' : 'Evidence'}
        </button>
      </div>
      {open && (
        <motion.div initial={{ opacity: 0, height: 0 }} animate={{ opacity: 1, height: 'auto' }} className="overflow-hidden px-3 pb-2">
          <EvidenceList evidence={edge.evidence} />
        </motion.div>
      )}
    </li>
  )
}

export default function ComponentDrawer({ projectId, componentId, onClose, onSelectNode, onBlastRadius }) {
  const [tab, setTab] = useState('Details')
  const { data, error, loading, reload } = useAsync(() => api.component(projectId, componentId), [projectId, componentId], { enabled: Boolean(componentId) })
  const t = NODE_TYPES[data?.type]
  const scenario = SCENARIO_FOR[data?.type]

  return (
    <Drawer open={Boolean(componentId)} onClose={onClose} title={data?.name || shortId(componentId || '')} subtitle={componentId} icon={t?.icon}>
      {loading && (
        <div className="space-y-3 p-5">
          <Skeleton className="h-5 w-1/2" />
          <Skeleton className="h-20 w-full" />
          <Skeleton className="h-32 w-full" />
        </div>
      )}
      {error && <div className="p-5"><ErrorState error={error} onRetry={reload} /></div>}
      {data && !loading && (
        <div>
          <div className="flex flex-wrap items-center gap-2 px-5 pt-4">
            <TypeBadge type={data.type} />
            {data.file && <Badge className="bg-neutral-bg text-neutral-ink"><span className="num">{data.file}{data.line ? `:${data.line}` : ''}</span></Badge>}
          </div>
          <div className="flex flex-wrap gap-2 px-5 pt-3">
            <Button size="sm" icon={Zap} onClick={() => onBlastRadius(componentId)}>Show blast radius</Button>
            {scenario && (
              <Link to={`/prediction?scenario=${scenario}&target=${encodeURIComponent(componentId)}`}>
                <Button size="sm" variant="primary" icon={Radar}>Predict failure</Button>
              </Link>
            )}
          </div>
          <div className="mt-4 flex gap-1 border-b border-edge px-5" role="tablist">
            {TABS.map((name) => (
              <button key={name} role="tab" aria-selected={tab === name} onClick={() => setTab(name)} className={`relative px-3 py-2 text-xs font-medium ${tab === name ? 'text-ink' : 'text-faint hover:text-muted'}`}>
                {name}
                {name === 'Findings' && data.findings.length > 0 && <span className="num ml-1 text-negative">{data.findings.length}</span>}
                {/* Underline tab: the 1px accent indicator lies on the drawer's bottom rule. */}
                {tab === name && <motion.span layoutId="drawer-tab" className="absolute inset-x-1 -bottom-px h-px bg-accent" />}
              </button>
            ))}
          </div>
          <motion.div key={tab} initial={{ opacity: 0, y: 4 }} animate={{ opacity: 1, y: 0 }} className="p-5">
            {tab === 'Details' && (
              <div className="space-y-4">
                <div className="grid grid-cols-3 gap-2">
                  {[
                    ['Criticality', data.metrics.criticality],
                    ['Dependents', data.metrics.dependents],
                    ['Depends on', data.metrics.dependencies],
                    ['Routes affected', data.metrics.routes_depending],
                    ['Tests covering', data.metrics.tests_covering],
                    ['Betweenness', data.metrics.betweenness],
                  ].map(([k, v]) => (
                    <div key={k} className="rounded-card border border-edge bg-ground p-2.5">
                      <div className="text-[11px] text-faint">{k}</div>
                      <div className="num mt-0.5 font-medium text-ink">{v ?? '—'}</div>
                    </div>
                  ))}
                </div>
                {data.data?.method && <Row k="HTTP" v={<span className="num">{data.data.method} {data.data.path}</span>} />}
                {data.data?.handler && <Row k="Handler" v={<button className="num text-accent hover:underline" onClick={() => onSelectNode(data.data.handler)}>{shortId(data.data.handler)}</button>} />}
                {data.data?.framework && <Row k="Framework" v={data.data.framework} />}
                {data.data?.engine && <Row k="Engine" v={data.data.engine} />}
                {data.data?.launcher_methods && <Row k="Starts via" v={data.data.launcher_methods.join(', ')} />}
                {data.data?.raises?.length > 0 && <Row k="Raises" v={data.data.raises.join(', ')} />}
                {data.data?.args?.length > 0 && <Row k="Arguments" v={<span className="num">{data.data.args.join(', ')}</span>} />}
                {data.data?.nodeid && <Row k="Pytest id" v={<span className="num break-all">{data.data.nodeid}</span>} />}
                {data.data?.sql?.length > 0 && (
                  <div>
                    <div className="mb-1 text-xs font-medium text-ink">SQL statements</div>
                    {data.data.sql.map((s, i) => (
                      <code key={i} className="num mb-1 block rounded bg-ground px-2 py-1 text-[11px] text-warning-ink">L{s.line}: {s.statement}</code>
                    ))}
                  </div>
                )}
                <div className="text-xs text-faint">
                  {data.upstream.length} components depend on this (upstream) · it depends on {data.downstream.length} (downstream).
                </div>
              </div>
            )}
            {tab === 'Relationships' && (
              <div className="space-y-4">
                <Section title={`Depends on (${data.outgoing.filter((e) => e.type !== 'contains').length})`}>
                  {data.outgoing.filter((e) => e.type !== 'contains').map((e) => <EdgeRow key={e.id} edge={e} otherId={e.target} direction="out" onSelectNode={onSelectNode} />)}
                </Section>
                <Section title={`Used by (${data.incoming.filter((e) => e.type !== 'contains').length})`}>
                  {data.incoming.filter((e) => e.type !== 'contains').map((e) => <EdgeRow key={e.id} edge={e} otherId={e.source} direction="in" onSelectNode={onSelectNode} />)}
                </Section>
              </div>
            )}
            {tab === 'Source' &&
              (data.source_excerpt ? (
                <pre className="num overflow-x-auto rounded-card border border-edge bg-well p-3 text-[11px] leading-relaxed text-muted">{data.source_excerpt}</pre>
              ) : (
                <p className="text-sm text-faint">No source excerpt for this component type (resources like databases and env variables are inferred from the code that uses them; see Relationships).</p>
              ))}
            {tab === 'Findings' &&
              (data.findings.length ? (
                <ul className="space-y-2">
                  {data.findings.map((f) => (
                    <li key={f.id} className="rounded-card border border-edge bg-ground p-3">
                      <div className="flex items-center gap-2"><SeverityBadge severity={f.severity} /><span className="text-sm text-ink">{f.title}</span></div>
                      <p className="mt-1 text-xs text-muted">{f.description}</p>
                    </li>
                  ))}
                </ul>
              ) : (
                <p className="text-sm text-faint">No findings attached to this component.</p>
              ))}
          </motion.div>
          <div className="px-5 pb-5">
            <button onClick={() => onSelectNode(componentId, true)} className="flex items-center gap-1.5 text-xs text-muted hover:text-ink">
              <Crosshair className="h-3 w-3" /> Center in graph
            </button>
          </div>
        </div>
      )}
    </Drawer>
  )
}

function Row({ k, v }) {
  return (
    <div className="flex gap-3 border-b border-rule py-1.5 text-sm last:border-b-0">
      <span className="w-24 shrink-0 text-faint">{k}</span>
      <span className="min-w-0 text-ink">{v}</span>
    </div>
  )
}

function Section({ title, children }) {
  return (
    <div>
      <div className="mb-1.5 text-xs font-medium text-ink">{title}</div>
      <ul className="space-y-1.5">{children}</ul>
    </div>
  )
}
