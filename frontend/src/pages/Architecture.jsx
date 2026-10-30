import { Background, Controls, MarkerType, MiniMap, ReactFlow, ReactFlowProvider, useReactFlow } from '@xyflow/react'
import { AnimatePresence, motion } from 'framer-motion'
import { Filter, LayoutGrid, Maximize2, Network, RotateCcw, Search, Sparkles, Table2, X, Zap } from 'lucide-react'
import { useCallback, useEffect, useMemo, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import ComponentDrawer, { EvidenceList } from '../components/graph/ComponentDrawer'
import GraphNode from '../components/graph/GraphNode'
import MultimodalAuditModal from '../components/MultimodalAuditModal'
import ProjectGate from '../components/ProjectGate'
import { Badge, Button, Card, ErrorState, EvidenceBadge, IconButton, Modal, PageHeader, Skeleton, TypeBadge } from '../components/ui'
import { useAsync } from '../hooks/useAsync'
import { useSettings } from '../hooks/useSettings'
import { api } from '../services/api'
import { EDGE_LABELS, NODE_TYPES } from '../utils/constants'
import { filterGraph, layout, neighbourhood, shortId } from '../utils/graph'

const nodeTypes = { dna: GraphNode }
const DEFAULT_TYPES = ['route', 'app', 'function', 'database', 'external_api', 'env_var', 'worker', 'test']
const DEFAULT_EDGES = ['calls', 'handled_by', 'served_by', 'runs_on_startup', 'uses_database', 'calls_http', 'reads_env', 'exercises', 'runs_worker']

export default function Architecture() {
  return (
    <ProjectGate>
      {(project) => (
        <ReactFlowProvider>
          <Explorer project={project} />
        </ReactFlowProvider>
      )}
    </ProjectGate>
  )
}

function Explorer({ project }) {
  const [params, setParams] = useSearchParams()
  const { settings } = useSettings()
  const isBright = settings.theme === 'bright'
  const arch = useAsync(() => api.architecture(project.id), [project.id, project.latest_run_id])
  const predictionId = params.get('prediction')
  const prediction = useAsync(() => api.prediction(predictionId), [predictionId], { enabled: Boolean(predictionId) })
  const [view, setView] = useState('graph')
  const [types, setTypes] = useState(new Set(DEFAULT_TYPES))
  const [edgeTypes, setEdgeTypes] = useState(new Set(DEFAULT_EDGES))
  const [query, setQuery] = useState('')
  const [selected, setSelected] = useState(params.get('focus'))
  const [drawerId, setDrawerId] = useState(params.get('focus'))
  const [blastOf, setBlastOf] = useState(null)
  const [edge, setEdge] = useState(null)
  const [showFilters, setShowFilters] = useState(false)
  const [auditModalOpen, setAuditModalOpen] = useState(false)
  const flow = useReactFlow()

  const components = arch.data?.components || []
  const edges = arch.data?.edges || []

  const graph = useMemo(() => filterGraph(components, edges, { types, edgeTypes, query }), [components, edges, types, edgeTypes, query])
  const positions = useMemo(() => layout(graph.nodes, graph.edges), [graph.nodes, graph.edges])

  // highlight state: prediction impact > blast radius > selection neighbourhood > search
  const highlight = useMemo(() => {
    const impact = prediction.data?.impact
    if (predictionId && impact) {
      const states = {}
      impact.seeds.forEach((s) => (states[s] = 'source'))
      impact.affected.forEach((a) => (states[a.id] = a.certainty === 'definite' ? 'impact-definite' : 'impact-possible'))
      impact.tests.forEach((t) => t.prediction !== 'pass' && (states[t.id] = t.prediction === 'fail' ? 'impact-definite' : 'impact-possible'))
      return { states, dimOthers: true, label: `Predicted impact: ${impact.scenario_label} on ${impact.target.name}` }
    }
    if (blastOf) {
      const { upstream } = neighbourhood(edges, blastOf)
      const states = { [blastOf]: 'source' }
      upstream.forEach((u) => (states[u] = 'impact-definite'))
      return { states, dimOthers: true, label: `Blast radius of ${shortId(blastOf)}: ${upstream.size} dependents (structural, ignores error handling)` }
    }
    if (selected) {
      const { upstream, downstream } = neighbourhood(edges, selected)
      const states = {}
      upstream.forEach((u) => (states[u] = 'upstream'))
      downstream.forEach((d) => (states[d] = 'downstream'))
      return { states, dimOthers: true, label: null }
    }
    if (graph.matches) {
      const states = {}
      graph.matches.forEach((m) => (states[m] = 'match'))
      return { states, dimOthers: true, label: `${graph.matches.size} matches` }
    }
    return { states: {}, dimOthers: false, label: null }
  }, [prediction.data, predictionId, blastOf, selected, edges, graph.matches])

  const rfNodes = useMemo(
    () =>
      graph.nodes.map((c) => {
        const state = highlight.states[c.id]
        return {
          id: c.id,
          type: 'dna',
          position: positions[c.id] || { x: 0, y: 0 },
          selected: c.id === selected,
          data: {
            id: c.id,
            type: c.type,
            label: c.type === 'function' ? c.name : c.type === 'test' ? c.name : c.name,
            sub: c.type === 'function' ? (c.data.module || '') : c.type === 'route' ? (shortId(c.data.handler || '')) : NODE_TYPES[c.type]?.label,
            criticality: c.type !== 'test' ? c.metrics?.criticality : 0,
            state,
            dimmed: highlight.dimOthers && !state && c.id !== selected,
          },
        }
      }),
    [graph.nodes, positions, highlight, selected],
  )

  const rfEdges = useMemo(
    () =>
      graph.edges.map((e) => {
        const s = highlight.states
        const active = (s[e.source] || e.source === selected) && (s[e.target] || e.target === selected)
        const impact = active && (String(s[e.source]).startsWith('impact') || s[e.target] === 'source')
        const color = impact ? '#e5566f' : active ? (isBright ? '#1d5fbf' : '#4a8ad6') : e.classification === 'inferred' ? '#3a3b40' : '#5a5c62'
        return {
          id: e.id,
          source: e.source,
          target: e.target,
          className: impact ? 'edge-flow' : '',
          style: { stroke: color, strokeWidth: active ? 1.8 : 1, opacity: highlight.dimOthers && !active ? 0.15 : 1, strokeDasharray: e.classification === 'inferred' && !impact ? '4 3' : undefined },
          markerEnd: { type: MarkerType.ArrowClosed, color, width: 14, height: 14 },
          data: e,
        }
      }),
    [graph.edges, highlight, selected, isBright],
  )

  const fit = useCallback(() => flow.fitView({ padding: 0.15, duration: 400 }), [flow])

  useEffect(() => {
    if (rfNodes.length) setTimeout(fit, 60)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [graph.nodes.length, types, arch.data])

  const focusNode = useCallback(
    (id, center = false) => {
      const c = components.find((x) => x.id === id)
      if (!c) return
      if (!types.has(c.type)) setTypes((t) => new Set([...t, c.type]))
      setSelected(id)
      setDrawerId(id)
      setBlastOf(null)
      if (center) {
        setTimeout(() => {
          const p = positions[id]
          if (p) flow.setCenter(p.x + 105, p.y + 23, { zoom: 1.2, duration: 500 })
        }, 50)
      }
    },
    [components, types, positions, flow],
  )

  useEffect(() => {
    const f = params.get('focus')
    if (f && components.length) focusNode(f, true)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [components.length])

  const reset = () => {
    setTypes(new Set(DEFAULT_TYPES))
    setEdgeTypes(new Set(DEFAULT_EDGES))
    setQuery('')
    setSelected(null)
    setDrawerId(null)
    setBlastOf(null)
    setParams({})
    setTimeout(fit, 80)
  }

  const toggle = (set, setter, key) => {
    const next = new Set(set)
    next.has(key) ? next.delete(key) : next.add(key)
    setter(next)
  }

  const counts = useMemo(() => {
    const out = {}
    components.forEach((c) => (out[c.type] = (out[c.type] || 0) + 1))
    return out
  }, [components])

  if (arch.error) return <ErrorState error={arch.error} onRetry={arch.reload} />

  return (
    <>
      <PageHeader
        title="Component graph"
        subtitle="Every node and edge comes from static analysis of the source. Click a node for evidence; edges point from a component to what it depends on."
        actions={
          <div className="flex items-center gap-2">
            <Button
              variant="secondary"
              size="sm"
              onClick={() => setAuditModalOpen(true)}
              className="gap-1.5"
            >
              <Sparkles className="h-3.5 w-3.5 text-faint" />
              Visual Diagram Audit
            </Button>
            <div className="flex rounded-control border border-edge bg-surface p-0.5">
              {[['graph', Network, 'Graph'], ['table', Table2, 'Table']].map(([v, Icon, label]) => (
                <button key={v} onClick={() => setView(v)} className={`relative flex items-center gap-1.5 rounded-full px-3 py-1.5 text-xs font-medium ${view === v ? 'bg-raised text-ink' : 'text-faint hover:text-muted'}`} aria-pressed={view === v}>
                  <Icon className="relative h-3.5 w-3.5" /> <span className="relative">{label}</span>
                </button>
              ))}
            </div>
          </div>
        }
      />

      <div className="mb-3 flex flex-wrap items-center gap-2">
        <div className="relative min-w-[220px] flex-1 sm:max-w-sm">
          <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-faint" />
          <input
            value={query}
            onChange={(e) => {
              setQuery(e.target.value)
              setSelected(null)
              setBlastOf(null)
            }}
            placeholder="Search components, files…"
            aria-label="Search components"
            className="h-9 w-full rounded-control border border-control bg-surface pl-9 pr-8 text-sm text-ink outline-none focus:border-accent"
          />
          {query && (
            <button aria-label="Clear search" className="absolute right-2 top-1/2 -translate-y-1/2 text-faint" onClick={() => setQuery('')}>
              <X className="h-4 w-4" />
            </button>
          )}
        </div>
        <Button size="md" icon={Filter} onClick={() => setShowFilters((s) => !s)} variant={showFilters ? 'primary' : 'secondary'}>
          Filters
        </Button>
        <Button icon={Maximize2} onClick={fit}>Fit</Button>
        <Button icon={RotateCcw} onClick={reset}>Reset</Button>
        {selected && !blastOf && (
          <Button icon={Zap} variant="danger" onClick={() => setBlastOf(selected)}>Blast radius</Button>
        )}
      </div>

      <AnimatePresence>
        {showFilters && (
          <motion.div initial={{ height: 0, opacity: 0 }} animate={{ height: 'auto', opacity: 1 }} exit={{ height: 0, opacity: 0 }} className="overflow-hidden">
            <Card className="mb-3 space-y-3 p-3">
              <div>
                <div className="mb-1.5 text-xs text-muted">Component types</div>
                <div className="flex flex-wrap gap-1.5">
                  {Object.entries(NODE_TYPES).filter(([k]) => counts[k]).map(([k, t]) => (
                    <button key={k} onClick={() => toggle(types, setTypes, k)} aria-pressed={types.has(k)}
                      className={`flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-xs transition-colors ${types.has(k) ? 'border-control bg-raised text-ink' : 'border-edge text-faint hover:text-muted'}`}>
                      <span className="h-2 w-2 rounded-full" style={{ background: t.color, opacity: types.has(k) ? 1 : 0.3 }} />
                      {t.label} <span className="num text-faint">{counts[k]}</span>
                    </button>
                  ))}
                </div>
              </div>
              <div>
                <div className="mb-1.5 text-xs text-muted">Relationship types</div>
                <div className="flex flex-wrap gap-1.5">
                  {Object.keys(EDGE_LABELS).map((k) => (
                    <button key={k} onClick={() => toggle(edgeTypes, setEdgeTypes, k)} aria-pressed={edgeTypes.has(k)}
                      className={`rounded-full border px-2.5 py-1 text-xs ${edgeTypes.has(k) ? 'border-control bg-raised text-ink' : 'border-edge text-faint hover:text-muted'}`}>
                      {EDGE_LABELS[k]}
                    </button>
                  ))}
                </div>
              </div>
            </Card>
          </motion.div>
        )}
      </AnimatePresence>

      <AnimatePresence>
        {highlight.label && (
          <motion.div initial={{ opacity: 0, y: -4 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0 }} className="mb-3 flex items-center justify-between gap-2 rounded-card border border-negative/25 bg-negative-bg px-3 py-2 text-sm text-ink">
            <span className="flex items-center gap-2"><Zap className="h-4 w-4 text-negative" /> {highlight.label}</span>
            <IconButton icon={X} label="Clear highlight" onClick={() => { setBlastOf(null); setQuery(''); if (predictionId) setParams({}) }} />
          </motion.div>
        )}
      </AnimatePresence>

      {arch.loading && !arch.data ? (
        <Skeleton className="h-[640px] w-full" />
      ) : view === 'graph' ? (
        <Card className="relative h-[calc(100vh-17rem)] min-h-[520px] overflow-hidden">
          {rfNodes.length === 0 ? (
            <div className="flex h-full items-center justify-center text-sm text-faint">No components match the current filters.</div>
          ) : (
            <ReactFlow
              nodes={rfNodes}
              edges={rfEdges}
              nodeTypes={nodeTypes}
              onNodeClick={(_, n) => focusNode(n.id)}
              onEdgeClick={(_, e) => setEdge(e.data)}
              onPaneClick={() => { setSelected(null); setBlastOf(null) }}
              nodesConnectable={false}
              nodesDraggable
              minZoom={0.1}
              maxZoom={2}
              proOptions={{ hideAttribution: true }}
              fitView
            >
              <Background color={isBright ? '#e0e2e6' : '#232326'} gap={22} />
              <Controls showInteractive={false} position="bottom-left" />
              <MiniMap pannable zoomable nodeColor={(n) => NODE_TYPES[n.data.type]?.color || '#555'} maskColor={isBright ? 'rgba(255,255,255,0.7)' : 'rgba(19,18,23,0.7)'} className="!hidden md:!block" />
            </ReactFlow>
          )}
          <Legend />
        </Card>
      ) : (
        <ComponentTable components={graph.nodes} matches={graph.matches} onSelect={(id) => focusNode(id)} />
      )}

      <ComponentDrawer
        projectId={project.id}
        componentId={drawerId}
        onClose={() => setDrawerId(null)}
        onSelectNode={(id, center) => focusNode(id, center || view === 'graph')}
        onBlastRadius={(id) => { setBlastOf(id); setSelected(id); setDrawerId(null) }}
      />

      <Modal open={Boolean(edge)} onClose={() => setEdge(null)} title={edge ? `${EDGE_LABELS[edge.type] || edge.type}` : ''} subtitle={edge ? `${shortId(edge.source)} → ${shortId(edge.target)}` : ''}>
        {edge && (
          <div className="space-y-3">
            <div className="flex items-center gap-2 text-sm text-muted">
              Evidence classification: <EvidenceBadge classification={edge.classification} />
            </div>
            <EvidenceList evidence={edge.evidence} />
            <div className="flex gap-2">
              <Button size="sm" onClick={() => { focusNode(edge.source, true); setEdge(null) }}>Open source</Button>
              <Button size="sm" onClick={() => { focusNode(edge.target, true); setEdge(null) }}>Open target</Button>
            </div>
          </div>
        )}
      </Modal>
      <MultimodalAuditModal open={auditModalOpen} onClose={() => setAuditModalOpen(false)} projectId={project.id} />
    </>
  )
}

function Legend() {
  const [open, setOpen] = useState(true)
  return (
    <div className="absolute right-3 top-3 z-10 w-48 rounded-card border border-edge bg-surface text-xs shadow-[0_12px_28px_rgba(0,0,0,0.2)]">
      <button onClick={() => setOpen((o) => !o)} className="flex w-full items-center justify-between px-3 py-2 font-medium text-ink" aria-expanded={open}>
        <span className="flex items-center gap-1.5"><LayoutGrid className="h-3.5 w-3.5 text-faint" /> Legend</span>
        <span className="text-faint">{open ? '–' : '+'}</span>
      </button>
      <AnimatePresence initial={false}>
        {open && (
          <motion.div initial={{ height: 0 }} animate={{ height: 'auto' }} exit={{ height: 0 }} className="overflow-hidden">
            <ul className="space-y-1 px-3 pb-2">
              {Object.entries(NODE_TYPES).filter(([k]) => k !== 'module').map(([k, t]) => (
                <li key={k} className="flex items-center gap-2 text-muted"><t.icon className="h-3 w-3" style={{ color: t.color }} /> {t.label}</li>
              ))}
            </ul>
            <div className="space-y-1 border-t border-rule px-3 py-2 text-muted">
              <div className="flex items-center gap-2"><span className="h-px w-5 bg-faint" /> confirmed edge</div>
              <div className="flex items-center gap-2"><span className="w-5 border-t border-dashed border-faint" /> inferred edge</div>
              <div className="flex items-center gap-2"><span className="h-2 w-2 rounded-sm bg-accent" /> depends on selection</div>
              <div className="flex items-center gap-2"><span className="h-2 w-2 rounded-sm bg-muted" /> selection depends on</div>
              <div className="flex items-center gap-2"><span className="h-2 w-2 rounded-sm bg-negative" /> failure impact</div>
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  )
}

function ComponentTable({ components, matches, onSelect }) {
  const [sort, setSort] = useState('criticality')
  const rows = [...components]
    .filter((c) => !matches || matches.has(c.id))
    .sort((a, b) => (sort === 'name' ? a.id.localeCompare(b.id) : (b.metrics?.[sort] || 0) - (a.metrics?.[sort] || 0)))
  return (
    <Card className="overflow-hidden">
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-edge text-left text-xs text-muted">
              {[['name', 'Component'], [null, 'Type'], [null, 'Location'], ['criticality', 'Criticality'], ['dependents', 'Dependents'], ['routes_depending', 'Routes'], ['tests_covering', 'Tests']].map(([key, label]) => (
                <th key={label} className="px-4 py-2.5 font-normal">
                  {key ? <button onClick={() => setSort(key)} className={sort === key ? 'text-ink' : 'hover:text-ink'}>{label}{sort === key ? ' ↓' : ''}</button> : label}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((c) => (
              <tr key={c.id} onClick={() => onSelect(c.id)} className="cursor-pointer border-b border-rule hover:bg-raised">
                <td className="num max-w-xs truncate px-4 py-2 text-xs text-ink">{shortId(c.id)}</td>
                <td className="px-4 py-2"><TypeBadge type={c.type} /></td>
                <td className="num px-4 py-2 text-xs text-faint">{c.file ? `${c.file}:${c.line}` : '—'}</td>
                <td className="num px-4 py-2">{c.type === 'test' ? '—' : c.metrics?.criticality}</td>
                <td className="num px-4 py-2">{c.metrics?.dependents}</td>
                <td className="num px-4 py-2">{c.metrics?.routes_depending}</td>
                <td className="num px-4 py-2">{c.metrics?.tests_covering}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="border-t border-rule px-4 py-2 text-xs text-faint">
        <Badge>{rows.length}</Badge> components shown
      </div>
    </Card>
  )
}
