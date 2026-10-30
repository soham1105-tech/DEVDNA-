import { motion } from 'framer-motion'
import {
  Boxes, CheckCircle2, Circle, Database, FileCode2, FlaskConical, Gauge, GitBranch, Globe, RefreshCw, Route, ShieldAlert, Sparkles, Cog,
} from 'lucide-react'
import { Link, useOutletContext } from 'react-router-dom'
import { Bar, BarChart, CartesianGrid, Cell, Pie, PieChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import { stagger } from '../animations/variants'
import AIPanel from '../components/AIPanel'
import { ArchitectureResult } from '../components/AIRenderers'
import AstScoreCard from '../components/AstScoreCard'
import ProjectGate from '../components/ProjectGate'
import { Badge, Button, Card, CardHeader, ErrorState, PageHeader, SeverityBadge, SkeletonCards, Stat, StatStrip } from '../components/ui'
import Welcome, { WORKFLOW } from '../components/Welcome'
import { useAnalysisFlow } from '../hooks/useAnalysisFlow'
import { useAstAnalysis } from '../hooks/useAstAnalysis'
import { useAsync } from '../hooks/useAsync'
import { useProject } from '../hooks/useProject'
import { useSettings } from '../hooks/useSettings'
import { api } from '../services/api'
import { EXPERIMENT_STATUS, NODE_TYPES } from '../utils/constants'
import { pct, timeAgo } from '../utils/format'

// Chart palette per theme. The accent goes on the main data element only.
const CHART = {
  dark: { tick: '#8f9196', grid: '#26262a', accent: '#4a8ad6', ink: '#f2f2f3', grey: '#6f7178' },
  bright: { tick: '#6b6d72', grid: '#e5e7ea', accent: '#1d5fbf', ink: '#111213', grey: '#9a9ca0' },
}
const STATUS_FILL = {
  prediction_confirmed: '#44a282', partially_confirmed: '#d9a441', prediction_contradicted: '#e5566f',
  inconclusive: '#8f9196', blocked: '#d9a441', failed: '#e5566f', running: '#4a8ad6', queued: '#4a8ad6',
}

function ChartTooltip({ active, payload, label, suffix = '' }) {
  if (!active || !payload?.length) return null
  return (
    <div className="rounded-card border border-edge bg-surface px-3 py-2 text-xs shadow-[0_12px_28px_rgba(0,0,0,0.2)]">
      <div className="text-muted">{label || payload[0].payload.label}</div>
      <div className="num mt-0.5 font-medium text-ink">{payload[0].value}{suffix}</div>
    </div>
  )
}

const SCENARIO_ICONS = { db_unavailable: Database, external_api_unavailable: Globe, worker_unavailable: Cog }

export default function Overview() {
  const { projects, loading } = useProject()
  const ctx = useOutletContext()
  if (!loading && projects.length === 0) return <Welcome onAnalyse={ctx.openNewProject} />
  return <ProjectGate>{(project) => <Dashboard project={project} />}</ProjectGate>
}

function Dashboard({ project }) {
  const { data, error, loading, reload } = useAsync(() => api.analytics(project.id), [project.id, project.latest_run_id])
  const drift = useAsync(() => api.drift(project.id), [project.id, project.latest_run_id])
  const latestAI = useAsync(() => api.latestArchitecture(project.id), [project.id, project.latest_run_id])
  const ast = useAstAnalysis(project.ast_repository)
  const flow = useAnalysisFlow()
  const { settings } = useSettings()
  const C = CHART[settings.theme === 'bright' ? 'bright' : 'dark']
  const axis = { stroke: C.tick, fontSize: 11, tickLine: false, axisLine: false }

  if (loading && !data) return <SkeletonCards count={8} />
  if (error) return <ErrorState error={error} onRetry={reload} />
  const a = data
  const cov = a.coverage || {}
  const totalComponents = Object.values(a.component_counts).reduce((x, y) => x + y, 0)
  const totalEdges = Object.values(a.edge_counts).reduce((x, y) => x + y, 0)
  const typeData = Object.entries(a.component_counts)
    .map(([type, count]) => ({ type, label: NODE_TYPES[type]?.label || type, count }))
    .sort((x, y) => y.count - x.count)
  const evidenceData = [
    { label: 'Confirmed', value: a.edge_classification.confirmed || 0, fill: C.accent },
    { label: 'Inferred', value: a.edge_classification.inferred || 0, fill: C.grey },
  ]
  const centrality = a.centrality.slice(0, 8).map((c) => ({ label: c.name.length > 26 ? `${c.name.slice(0, 25)}…` : c.name, full: c.id, value: c.criticality }))
  const outcomes = Object.entries(a.experiments.by_status).map(([status, count]) => ({ status, label: EXPERIMENT_STATUS[status]?.label || status, count }))
  const stepsDone = {
    discover: true,
    visualize: true,
    predict: a.predictions.total > 0,
    experiment: a.experiments.total > 0,
    compare: Object.keys(a.experiments.by_status).some((s) => ['prediction_confirmed', 'partially_confirmed', 'prediction_contradicted', 'inconclusive'].includes(s)),
  }
  const stepLinks = { discover: '/', visualize: '/architecture', predict: '/prediction', experiment: '/lab', compare: '/experiments' }

  return (
    <>
      <PageHeader
        title={project.name}
        subtitle={`${project.source_type === 'sample' ? 'Bundled demo' : project.source_type === 'upload' ? 'Uploaded ZIP' : project.source_type === 'github' ? 'GitHub repository' : 'Local folder'} · analysed ${timeAgo(project.latest_run?.finished_at)} · ${a.frameworks.join(', ') || 'no frameworks detected'}`}
        actions={
          <>
            <Badge className={project.trusted ? 'bg-positive-bg text-positive-ink' : 'bg-warning-bg text-warning-ink'}>
              {project.trusted ? 'Trusted sample' : 'Untrusted code'}
            </Badge>
            <Button icon={RefreshCw} loading={flow.busy} onClick={async () => { await flow.start('reanalyse', project); reload() }}>
              Re-analyse
            </Button>
          </>
        }
      />

      <motion.div variants={stagger} initial="initial" animate="animate" className="space-y-4">
        <Card className="p-3">
          <ol className="grid grid-cols-5 gap-1">
            {WORKFLOW.map((w) => (
              <li key={w.key}>
                <Link to={stepLinks[w.key]} className="flex flex-col items-center gap-1 rounded-card px-1 py-2 text-center transition-colors hover:bg-raised sm:flex-row sm:justify-center sm:gap-2">
                  {stepsDone[w.key] ? <CheckCircle2 className="h-4 w-4 text-positive" /> : <Circle className="h-4 w-4 text-control" />}
                  <span className={`text-xs font-medium sm:text-sm ${stepsDone[w.key] ? 'text-ink' : 'text-faint'}`}>{w.label}</span>
                </Link>
              </li>
            ))}
          </ol>
        </Card>

        <StatStrip>
          <Stat label="Components" value={totalComponents} hint={`${a.component_counts.function || 0} functions · ${a.component_counts.class || 0} classes`} icon={Boxes} />
          <Stat label="Relationships" value={totalEdges} hint={`${a.edge_classification.confirmed || 0} confirmed · ${a.edge_classification.inferred || 0} inferred`} icon={GitBranch} />
          <Stat label="Routes" value={cov.routes ?? 0} hint={`${cov.routes_with_tests ?? 0} covered by tests`} icon={Route} />
          <Stat label="Open findings" value={a.findings.open} hint={`${a.findings.by_severity.high || 0} high · ${a.findings.by_severity.medium || 0} medium`} icon={ShieldAlert} />
          <Stat label="Experiments" value={a.experiments.total} hint={`${a.predictions.total} predictions`} icon={FlaskConical} />
          <AstScoreCard ast={ast} repository={project.ast_repository} />
        </StatStrip>

        <Card>
          <CardHeader title="Analysis drift" subtitle="Changes since the previous completed run" icon={GitBranch} />
          {drift.loading && !drift.data ? (
            <div className="space-y-2 p-4"><SkeletonCards count={2} /></div>
          ) : drift.error ? (
            <div className="p-4"><ErrorState error={drift.error} onRetry={drift.reload} /></div>
          ) : !drift.data?.has_comparison ? (
            <p className="p-4 text-sm text-muted">A second completed analysis is needed to show drift.</p>
          ) : (
            <div className="grid gap-4 p-4 md:grid-cols-3">
              <div>
                <h3 className="mb-2 text-xs font-medium text-positive">Routes added ({drift.data.routes_added.length})</h3>
                {drift.data.routes_added.length ? <ul className="space-y-1 text-xs text-muted">{drift.data.routes_added.map((route) => <li key={route.id} className="num">{route.name}</li>)}</ul> : <p className="text-xs text-faint">None</p>}
              </div>
              <div>
                <h3 className="mb-2 text-xs font-medium text-negative">Routes removed ({drift.data.routes_removed.length})</h3>
                {drift.data.routes_removed.length ? <ul className="space-y-1 text-xs text-muted">{drift.data.routes_removed.map((route) => <li key={route.id} className="num">{route.name}</li>)}</ul> : <p className="text-xs text-faint">None</p>}
              </div>
              <div>
                <h3 className="mb-2 text-xs font-medium text-ink">Blast radius grew</h3>
                {drift.data.blast_radius_growth.length ? <ul className="space-y-1 text-xs text-muted">{drift.data.blast_radius_growth.slice(0, 5).map((component) => <li key={component.id}>{component.name} <span className="text-faint">+{component.growth} dependents</span></li>)}</ul> : <p className="text-xs text-faint">No components grew.</p>}
              </div>
            </div>
          )}
        </Card>

        {project.source_type === 'sample' ? (
          <Card className="p-4">
            <div className="flex flex-col justify-between gap-2 sm:flex-row sm:items-center">
              <div>
                <div className="flex items-center gap-1.5 text-xs font-medium text-faint">
                  <Sparkles className="h-3.5 w-3.5" /> 3-Minute Hackathon Demo Scenarios
                </div>
                <p className="mt-0.5 text-xs text-muted sm:text-sm">
                  Click any scenario to predict its blast radius, inspect cited evidence, and prove it with real pytest execution:
                </p>
              </div>
              <Link to="/lab" className="shrink-0 text-xs font-medium text-accent hover:underline">
                Open What-If Lab &rarr;
              </Link>
            </div>
            <div className="mt-3 grid gap-3 sm:grid-cols-3">
              {[
                {
                  to: '/lab?scenario=db_unavailable', icon: Database, title: 'Database Outage', verdict: '100% Confirmed', verdictCls: 'text-positive',
                  body: 'Simulates SQLite crash. Cascades through startup into 6/6 routes and 8 failing tests.',
                },
                {
                  to: '/lab?scenario=external_api_unavailable', icon: Globe, title: 'Shipping API Outage', verdict: 'Partially Confirmed', verdictCls: 'text-warning',
                  body: 'Injects HTTP ConnectError. Discovers FastAPI 422 validation short-circuit before handler.',
                },
                {
                  to: '/lab?scenario=worker_unavailable', icon: Cog, title: 'Worker Unavailable', verdict: 'Handled Error', verdictCls: 'text-muted',
                  body: 'Thread worker never starts. Reveals try/except handler and reports uncertainty.',
                },
              ].map((s) => (
                <Link
                  key={s.to}
                  to={s.to}
                  className="group rounded-card border border-edge bg-ground p-3 transition-colors hover:border-control"
                >
                  <div className="flex items-center justify-between text-xs font-medium text-ink">
                    <span className="flex items-center gap-1.5"><s.icon className="h-3.5 w-3.5 text-faint" /> {s.title}</span>
                    <span className={`text-[11px] ${s.verdictCls}`}>{s.verdict}</span>
                  </div>
                  <p className="mt-1 text-xs text-faint">{s.body}</p>
                </Link>
              ))}
            </div>
          </Card>
        ) : (
          <Card className="p-4">
            <Link to="/lab" className="text-xs font-medium text-accent hover:underline">
              Open What-If Lab &rarr;
            </Link>
          </Card>
        )}

        <div className="grid gap-4 lg:grid-cols-3">
          <Card className="lg:col-span-2">
            <CardHeader title="Most critical components" subtitle="Structural criticality score (0–100), a heuristic, not a failure probability" icon={Gauge}
              action={<Link to="/settings#formula" className="text-xs text-accent hover:underline">Formula</Link>} />
            <div className="h-72 p-2">
              <ResponsiveContainer>
                <BarChart data={centrality} layout="vertical" margin={{ left: 8, right: 24, top: 8, bottom: 8 }} barCategoryGap={6}>
                  <CartesianGrid horizontal={false} stroke={C.grid} />
                  <XAxis type="number" domain={[0, 100]} {...axis} />
                  <YAxis type="category" dataKey="label" width={170} {...axis} tick={{ fill: C.tick, fontSize: 11 }} />
                  <Tooltip cursor={{ fill: 'rgba(128,128,128,0.06)' }} content={<ChartTooltip />} />
                  <Bar isAnimationActive={false} dataKey="value" fill={C.accent} radius={[0, 2, 2, 0]} maxBarSize={18} />
                </BarChart>
              </ResponsiveContainer>
            </div>
          </Card>
          <Card className="flex flex-col">
            <CardHeader title="Edge evidence" subtitle="How each relationship was established" icon={GitBranch} />
            <div className="min-h-0 flex-1 p-2">
              <ResponsiveContainer>
                <PieChart>
                  <Pie isAnimationActive={false} data={evidenceData} dataKey="value" nameKey="label" innerRadius={48} outerRadius={70} paddingAngle={2} stroke="var(--color-surface)" strokeWidth={2}>
                    {evidenceData.map((d) => <Cell key={d.label} fill={d.fill} />)}
                  </Pie>
                  <Tooltip content={<ChartTooltip />} />
                </PieChart>
              </ResponsiveContainer>
            </div>
            <div className="flex justify-center gap-4 pb-4 text-xs text-muted">
              {evidenceData.map((d) => (
                <span key={d.label} className="flex items-center gap-1.5">
                  <span className="h-2.5 w-2.5 rounded-sm" style={{ background: d.fill }} /> {d.label} <span className="num text-faint">{d.value}</span>
                </span>
              ))}
            </div>
          </Card>
        </div>

        <div className="grid gap-4 lg:grid-cols-3">
          <Card className="flex flex-col">
            <CardHeader title="Components by type" icon={Boxes} />
            <div className="min-h-0 flex-1 p-2">
              <ResponsiveContainer>
                <BarChart data={typeData} margin={{ left: -16, right: 8, top: 8, bottom: 30 }}>
                  <CartesianGrid vertical={false} stroke={C.grid} />
                  <XAxis dataKey="label" {...axis} interval={0} angle={-35} textAnchor="end" height={50} tick={{ fill: C.tick, fontSize: 11 }} />
                  <YAxis allowDecimals={false} {...axis} />
                  <Tooltip cursor={{ fill: 'rgba(128,128,128,0.06)' }} content={<ChartTooltip />} />
                  <Bar isAnimationActive={false} dataKey="count" fill={C.grey} radius={[2, 2, 0, 0]} maxBarSize={28} />
                </BarChart>
              </ResponsiveContainer>
            </div>
          </Card>
          <Card>
            <CardHeader title="Analysis coverage" subtitle="What static analysis could and could not resolve" icon={FileCode2} />
            <dl className="grid grid-cols-2 gap-x-4 gap-y-3 p-4 text-sm">
              {[
                ['Files scanned', a.scan.file_count],
                ['Python parsed', `${cov.parsed_files}/${cov.python_files}`],
                ['Call resolution', pct(cov.call_resolution_rate)],
                ['Unresolved calls', cov.unresolved_calls],
                ['Tests found', cov.tests],
                ['Routes tested', `${cov.routes_with_tests}/${cov.routes}`],
                ['Secrets skipped', a.scan.skipped_sensitive],
                ['Dependencies', a.dependencies.length],
              ].map(([k, v]) => (
                <div key={k}>
                  <dt className="text-xs text-muted">{k}</dt>
                  <dd className="num mt-0.5 font-medium text-ink">{v ?? '—'}</dd>
                </div>
              ))}
            </dl>
            <div className="border-t border-rule px-4 py-3 text-xs text-muted">
              Languages:{' '}
              {Object.entries(a.scan.languages || {}).map(([lang, n]) => (
                <span key={lang} className="mr-2">
                  {lang} <span className="num text-faint">{n}</span>
                  {lang === 'Python' ? <span className="ml-1 text-positive">analysed</span> : <span className="ml-1 text-faint">detected only</span>}
                </span>
              ))}
            </div>
          </Card>
          <Card className="flex flex-col">
            <CardHeader title="Experiment outcomes" subtitle="Prediction vs observation" icon={FlaskConical} />
            {/* The empty chart is still drawn: axes and zero line, no padded range. */}
            <div className="min-h-0 flex-1 p-2">
              <ResponsiveContainer>
                <BarChart data={outcomes} layout="vertical" margin={{ left: 8, right: 16, top: 8, bottom: 8 }}>
                  <XAxis type="number" allowDecimals={false} {...axis} />
                  <YAxis type="category" dataKey="label" width={130} {...axis} tick={{ fill: C.tick, fontSize: 11 }} />
                  <Tooltip cursor={{ fill: 'rgba(128,128,128,0.06)' }} content={<ChartTooltip />} />
                  <Bar isAnimationActive={false} dataKey="count" radius={[0, 2, 2, 0]} maxBarSize={18}>
                    {outcomes.map((o) => <Cell key={o.status} fill={STATUS_FILL[o.status] || '#8f9196'} />)}
                  </Bar>
                </BarChart>
              </ResponsiveContainer>
            </div>
            {outcomes.length === 0 && (
              <p className="px-4 pb-3 text-xs text-faint">
                No experiments yet. <Link to="/lab" className="text-accent hover:underline">Open the What-If Lab</Link>
              </p>
            )}
          </Card>
        </div>

        <div className="grid gap-4 items-start lg:grid-cols-2">
          <AIPanel
            title="Architecture explanation"
            subtitle="Gemma explains the discovered architecture from cited evidence"
            purpose="Architecture explanation"
            fetcher={(body) => api.explainArchitecture(project.id, body)}
            initial={latestAI.data}
            key={latestAI.data?.meta?.interaction_id || 'none'}
            render={(r) => <ArchitectureResult r={r} />}
          />
          <Card>
            <CardHeader title="Top findings" icon={ShieldAlert} action={<Link to="/findings" className="text-xs text-accent hover:underline">View all</Link>} />
            <ul className="divide-y divide-rule">
              {a.findings.recent.map((f) => (
                <li key={f.id}>
                  <Link to={`/findings?open=${f.id}`} className="flex items-start gap-3 px-4 py-3 transition-colors hover:bg-raised">
                    <SeverityBadge severity={f.severity} />
                    <div className="min-w-0">
                      <div className="truncate text-sm text-ink">{f.title}</div>
                      <div className="truncate text-xs text-faint">{f.component_id || f.rule} · confidence {f.confidence}</div>
                    </div>
                  </Link>
                </li>
              ))}
              {a.findings.recent.length === 0 && <li className="p-4 text-sm text-muted">No findings.</li>}
            </ul>
          </Card>
        </div>
      </motion.div>
    </>
  )
}
