import { motion } from 'framer-motion'
import {
  Boxes, CheckCircle2, Circle, FileCode2, FlaskConical, Gauge, GitBranch, RefreshCw, Route, ShieldAlert, Sparkles,
} from 'lucide-react'
import { Link, useOutletContext } from 'react-router-dom'
import { Bar, BarChart, CartesianGrid, Cell, Pie, PieChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import { stagger } from '../animations/variants'
import AIPanel from '../components/AIPanel'
import { ArchitectureResult } from '../components/AIRenderers'
import AstScoreCard from '../components/AstScoreCard'
import ProjectGate from '../components/ProjectGate'
import { Badge, Button, Card, CardHeader, ErrorState, PageHeader, SeverityBadge, SkeletonCards, Stat } from '../components/ui'
import Welcome, { WORKFLOW } from '../components/Welcome'
import { useAnalysisFlow } from '../hooks/useAnalysisFlow'
import { useAstAnalysis } from '../hooks/useAstAnalysis'
import { useAsync } from '../hooks/useAsync'
import { useProject } from '../hooks/useProject'
import { api } from '../services/api'
import { EXPERIMENT_STATUS, NODE_TYPES } from '../utils/constants'
import { pct, timeAgo } from '../utils/format'

const VIOLET = '#8b5cf6'
const CYAN = '#0891b2'
const STATUS_FILL = {
  prediction_confirmed: '#059669', partially_confirmed: '#d97706', prediction_contradicted: '#e11d48',
  inconclusive: '#64748b', blocked: '#ea580c', failed: '#be123c', running: CYAN, queued: CYAN,
}

function ChartTooltip({ active, payload, label, suffix = '' }) {
  if (!active || !payload?.length) return null
  return (
    <div className="rounded-lg border border-white/10 bg-ink-800 px-3 py-2 text-xs shadow-xl">
      <div className="text-ink-300">{label || payload[0].payload.label}</div>
      <div className="mt-0.5 font-medium tabular-nums text-ink-100">{payload[0].value}{suffix}</div>
    </div>
  )
}

const axis = { stroke: '#5d6378', fontSize: 11, tickLine: false, axisLine: false }

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
    { label: 'Confirmed', value: a.edge_classification.confirmed || 0, fill: VIOLET },
    { label: 'Inferred', value: a.edge_classification.inferred || 0, fill: CYAN },
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
        eyebrow="Overview"
        title={project.name}
        subtitle={`${project.source_type === 'sample' ? 'Bundled demo' : project.source_type === 'upload' ? 'Uploaded ZIP' : project.source_type === 'github' ? 'GitHub repository' : 'Local folder'} · analysed ${timeAgo(project.latest_run?.finished_at)} · ${a.frameworks.join(', ') || 'no frameworks detected'}`}
        actions={
          <>
            <Badge className={project.trusted ? 'border-emerald-400/25 bg-emerald-400/10 text-emerald-200' : 'border-orange-400/25 bg-orange-400/10 text-orange-200'}>
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
                <Link to={stepLinks[w.key]} className="flex flex-col items-center gap-1 rounded-lg px-1 py-2 text-center transition-colors hover:bg-ink-700/50 sm:flex-row sm:justify-center sm:gap-2">
                  {stepsDone[w.key] ? <CheckCircle2 className="h-4 w-4 text-emerald-300" /> : <Circle className="h-4 w-4 text-ink-500" />}
                  <span className={`text-xs font-medium sm:text-sm ${stepsDone[w.key] ? 'text-ink-100' : 'text-ink-400'}`}>{w.label}</span>
                </Link>
              </li>
            ))}
          </ol>
        </Card>

        <div className="grid grid-cols-2 gap-3 lg:grid-cols-3 xl:grid-cols-6">
          <Stat label="Components" value={totalComponents} hint={`${a.component_counts.function || 0} functions · ${a.component_counts.class || 0} classes`} icon={Boxes} />
          <Stat label="Relationships" value={totalEdges} hint={`${a.edge_classification.confirmed || 0} confirmed · ${a.edge_classification.inferred || 0} inferred`} icon={GitBranch} accent="cyan" />
          <Stat label="Routes" value={cov.routes ?? 0} hint={`${cov.routes_with_tests ?? 0} covered by tests`} icon={Route} accent="cyan" />
          <Stat label="Open findings" value={a.findings.open} hint={`${a.findings.by_severity.high || 0} high · ${a.findings.by_severity.medium || 0} medium`} icon={ShieldAlert} accent="rose" />
          <Stat label="Experiments" value={a.experiments.total} hint={`${a.predictions.total} predictions`} icon={FlaskConical} accent="emerald" />
          <AstScoreCard ast={ast} repository={project.ast_repository} />
        </div>

        <Card>
          <CardHeader title="Analysis drift" subtitle="Changes since the previous completed run" icon={GitBranch} />
          {drift.loading && !drift.data ? (
            <div className="space-y-2 p-4"><SkeletonCards count={2} /></div>
          ) : drift.error ? (
            <div className="p-4"><ErrorState error={drift.error} onRetry={drift.reload} /></div>
          ) : !drift.data?.has_comparison ? (
            <p className="p-4 text-sm text-ink-300">A second completed analysis is needed to show drift.</p>
          ) : (
            <div className="grid gap-4 p-4 md:grid-cols-3">
              <div>
                <h3 className="mb-2 text-xs font-semibold uppercase tracking-wider text-emerald-200">Routes added ({drift.data.routes_added.length})</h3>
                {drift.data.routes_added.length ? <ul className="space-y-1 text-xs text-ink-200">{drift.data.routes_added.map((route) => <li key={route.id} className="font-mono">{route.name}</li>)}</ul> : <p className="text-xs text-ink-400">None</p>}
              </div>
              <div>
                <h3 className="mb-2 text-xs font-semibold uppercase tracking-wider text-rose-200">Routes removed ({drift.data.routes_removed.length})</h3>
                {drift.data.routes_removed.length ? <ul className="space-y-1 text-xs text-ink-200">{drift.data.routes_removed.map((route) => <li key={route.id} className="font-mono">{route.name}</li>)}</ul> : <p className="text-xs text-ink-400">None</p>}
              </div>
              <div>
                <h3 className="mb-2 text-xs font-semibold uppercase tracking-wider text-violet-200">Blast radius grew</h3>
                {drift.data.blast_radius_growth.length ? <ul className="space-y-1 text-xs text-ink-200">{drift.data.blast_radius_growth.slice(0, 5).map((component) => <li key={component.id}>{component.name} <span className="text-ink-400">+{component.growth} dependents</span></li>)}</ul> : <p className="text-xs text-ink-400">No components grew.</p>}
              </div>
            </div>
          )}
        </Card>

        {project.source_type === 'sample' ? (
          <Card className="border-violet-500/20 bg-gradient-to-r from-violet-500/[0.07] via-cyan-500/[0.04] to-ink-900/60 p-4">
            <div className="flex flex-col justify-between gap-2 sm:flex-row sm:items-center">
              <div>
                <div className="flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wider text-violet-300">
                  <Sparkles className="h-3.5 w-3.5" /> 3-Minute Hackathon Demo Scenarios
                </div>
                <p className="mt-0.5 text-xs text-ink-300 sm:text-sm">
                  Click any scenario to predict its blast radius, inspect cited evidence, and prove it with real pytest execution:
                </p>
              </div>
              <Link to="/lab" className="shrink-0 text-xs font-medium text-cyan-300 hover:underline">
                Open What-If Lab &rarr;
              </Link>
            </div>
            <div className="mt-3 grid gap-3 sm:grid-cols-3">
              <Link
                to="/lab?scenario=db_unavailable"
                className="group rounded-xl border border-white/[0.06] bg-ink-900/70 p-3 transition-colors hover:border-violet-400/40 hover:bg-ink-800/80"
              >
                <div className="flex items-center justify-between text-xs font-medium text-ink-100">
                  <span>💥 Database Outage</span>
                  <span className="text-[10px] text-emerald-400">100% Confirmed</span>
                </div>
                <p className="mt-1 text-xs text-ink-400">
                  Simulates SQLite crash. Cascades through startup into 6/6 routes and 8 failing tests.
                </p>
              </Link>

              <Link
                to="/lab?scenario=external_api_unavailable"
                className="group rounded-xl border border-white/[0.06] bg-ink-900/70 p-3 transition-colors hover:border-cyan-400/40 hover:bg-ink-800/80"
              >
                <div className="flex items-center justify-between text-xs font-medium text-ink-100">
                  <span>🌐 Shipping API Outage</span>
                  <span className="text-[10px] text-amber-300">Partially Confirmed</span>
                </div>
                <p className="mt-1 text-xs text-ink-400">
                  Injects HTTP ConnectError. Discovers FastAPI 422 validation short-circuit before handler.
                </p>
              </Link>

              <Link
                to="/lab?scenario=worker_unavailable"
                className="group rounded-xl border border-white/[0.06] bg-ink-900/70 p-3 transition-colors hover:border-emerald-400/40 hover:bg-ink-800/80"
              >
                <div className="flex items-center justify-between text-xs font-medium text-ink-100">
                  <span>⚙️ Worker Unavailable</span>
                  <span className="text-[10px] text-violet-300">Handled Error</span>
                </div>
                <p className="mt-1 text-xs text-ink-400">
                  Thread worker never starts. Reveals try/except handler and reports uncertainty.
                </p>
              </Link>
            </div>
          </Card>
        ) : (
          <Card className="p-4">
            <Link to="/lab" className="text-xs font-medium text-cyan-300 hover:underline">
              Open What-If Lab &rarr;
            </Link>
          </Card>
        )}

        <div className="grid gap-4 lg:grid-cols-3">
          <Card className="lg:col-span-2">
            <CardHeader title="Most critical components" subtitle="Structural criticality score (0–100), a heuristic, not a failure probability" icon={Gauge}
              action={<Link to="/settings#formula" className="text-xs text-violet-300 hover:underline">Formula</Link>} />
            <div className="h-72 p-2">
              <ResponsiveContainer>
                <BarChart data={centrality} layout="vertical" margin={{ left: 8, right: 24, top: 8, bottom: 8 }} barCategoryGap={6}>
                  <CartesianGrid horizontal={false} stroke="#262a38" />
                  <XAxis type="number" domain={[0, 100]} {...axis} />
                  <YAxis type="category" dataKey="label" width={170} {...axis} tick={{ fill: '#b8bdcf', fontSize: 11 }} />
                  <Tooltip cursor={{ fill: 'rgba(255,255,255,0.03)' }} content={<ChartTooltip />} />
                  <Bar isAnimationActive={false} dataKey="value" fill={VIOLET} radius={[0, 4, 4, 0]} maxBarSize={18} />
                </BarChart>
              </ResponsiveContainer>
            </div>
          </Card>
          <Card>
            <CardHeader title="Edge evidence" subtitle="How each relationship was established" icon={GitBranch} />
            <div className="h-48 p-2">
              <ResponsiveContainer>
                <PieChart>
                  <Pie isAnimationActive={false} data={evidenceData} dataKey="value" nameKey="label" innerRadius={48} outerRadius={70} paddingAngle={2} stroke="#0f1117" strokeWidth={2}>
                    {evidenceData.map((d) => <Cell key={d.label} fill={d.fill} />)}
                  </Pie>
                  <Tooltip content={<ChartTooltip />} />
                </PieChart>
              </ResponsiveContainer>
            </div>
            <div className="flex justify-center gap-4 pb-4 text-xs text-ink-200">
              {evidenceData.map((d) => (
                <span key={d.label} className="flex items-center gap-1.5">
                  <span className="h-2.5 w-2.5 rounded-sm" style={{ background: d.fill }} /> {d.label} <span className="tabular-nums text-ink-400">{d.value}</span>
                </span>
              ))}
            </div>
          </Card>
        </div>

        <div className="grid gap-4 lg:grid-cols-3">
          <Card>
            <CardHeader title="Components by type" icon={Boxes} />
            <div className="h-64 p-2">
              <ResponsiveContainer>
                <BarChart data={typeData} margin={{ left: -16, right: 8, top: 8, bottom: 30 }}>
                  <CartesianGrid vertical={false} stroke="#262a38" />
                  <XAxis dataKey="label" {...axis} interval={0} angle={-35} textAnchor="end" height={50} tick={{ fill: '#b8bdcf', fontSize: 10 }} />
                  <YAxis allowDecimals={false} {...axis} />
                  <Tooltip cursor={{ fill: 'rgba(255,255,255,0.03)' }} content={<ChartTooltip />} />
                  <Bar isAnimationActive={false} dataKey="count" fill={VIOLET} radius={[4, 4, 0, 0]} maxBarSize={28} />
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
                  <dt className="text-xs text-ink-400">{k}</dt>
                  <dd className="mt-0.5 font-medium tabular-nums text-ink-100">{v ?? '—'}</dd>
                </div>
              ))}
            </dl>
            <div className="border-t border-white/[0.05] px-4 py-3 text-xs text-ink-300">
              Languages:{' '}
              {Object.entries(a.scan.languages || {}).map(([lang, n]) => (
                <span key={lang} className="mr-2">
                  {lang} <span className="text-ink-500">{n}</span>
                  {lang === 'Python' ? <span className="ml-1 text-emerald-300">analysed</span> : <span className="ml-1 text-ink-500">detected only</span>}
                </span>
              ))}
            </div>
          </Card>
          <Card>
            <CardHeader title="Experiment outcomes" subtitle="Prediction vs observation" icon={FlaskConical} />
            {outcomes.length === 0 ? (
              <div className="p-6 text-center text-sm text-ink-300">
                No experiments yet. <Link to="/lab" className="text-violet-300 hover:underline">Open the What-If Lab</Link>
              </div>
            ) : (
              <div className="h-64 p-2">
                <ResponsiveContainer>
                  <BarChart data={outcomes} layout="vertical" margin={{ left: 8, right: 16, top: 8, bottom: 8 }}>
                    <XAxis type="number" allowDecimals={false} {...axis} />
                    <YAxis type="category" dataKey="label" width={130} {...axis} tick={{ fill: '#b8bdcf', fontSize: 11 }} />
                    <Tooltip cursor={{ fill: 'rgba(255,255,255,0.03)' }} content={<ChartTooltip />} />
                    <Bar isAnimationActive={false} dataKey="count" radius={[0, 4, 4, 0]} maxBarSize={18}>
                      {outcomes.map((o) => <Cell key={o.status} fill={STATUS_FILL[o.status] || '#64748b'} />)}
                    </Bar>
                  </BarChart>
                </ResponsiveContainer>
              </div>
            )}
          </Card>
        </div>

        <div className="grid gap-4 lg:grid-cols-2">
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
            <CardHeader title="Top findings" icon={ShieldAlert} action={<Link to="/findings" className="text-xs text-violet-300 hover:underline">View all</Link>} />
            <ul className="divide-y divide-white/[0.04]">
              {a.findings.recent.map((f) => (
                <li key={f.id}>
                  <Link to={`/findings?open=${f.id}`} className="flex items-start gap-3 px-4 py-3 transition-colors hover:bg-ink-700/30">
                    <SeverityBadge severity={f.severity} />
                    <div className="min-w-0">
                      <div className="truncate text-sm text-ink-100">{f.title}</div>
                      <div className="truncate text-xs text-ink-400">{f.component_id || f.rule} · confidence {f.confidence}</div>
                    </div>
                  </Link>
                </li>
              ))}
              {a.findings.recent.length === 0 && <li className="p-4 text-sm text-ink-300">No findings.</li>}
            </ul>
          </Card>
        </div>
      </motion.div>
    </>
  )
}
