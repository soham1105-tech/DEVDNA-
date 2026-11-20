import { motion } from 'framer-motion'
import { ArrowRight, Beaker, CheckCircle2, History, Network, Radar, Sparkles } from 'lucide-react'
import { useEffect, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import AIPanel from '../components/AIPanel'
import { FailureAnalysisResult } from '../components/AIRenderers'
import ImpactView from '../components/ImpactView'
import ProjectGate from '../components/ProjectGate'
import ScenarioPicker from '../components/ScenarioPicker'
import { Badge, Button, Card, CardHeader, ErrorState, PageHeader, Skeleton } from '../components/ui'
import { useAsync } from '../hooks/useAsync'
import { api } from '../services/api'
import { timeAgo } from '../utils/format'
import { shortId } from '../utils/graph'

export default function Prediction() {
  return <ProjectGate>{(project) => <PredictionPage project={project} />}</ProjectGate>
}

function PredictionPage({ project }) {
  const [params, setParams] = useSearchParams()
  const scenarios = useAsync(() => api.scenarios(project.id), [project.id, project.latest_run_id])
  const history = useAsync(() => api.predictions(project.id), [project.id])
  const [scenario, setScenario] = useState(params.get('scenario') || 'db_unavailable')
  const [target, setTarget] = useState(params.get('target') || '')
  const [prediction, setPrediction] = useState(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)

  // default target = most critical eligible component
  useEffect(() => {
    const s = scenarios.data?.find((x) => x.id === scenario)
    if (s && !s.targets.some((t) => t.id === target)) setTarget(s.targets[0]?.id || '')
  }, [scenarios.data, scenario, target])

  useEffect(() => {
    const id = params.get('id')
    if (id) api.prediction(id).then(setPrediction).catch(setError)
  }, [params])

  const compute = async () => {
    setBusy(true)
    setError(null)
    try {
      const p = await api.createPrediction(project.id, scenario, target)
      setPrediction(p)
      setParams({ id: p.id })
      history.reload({ silent: true })
    } catch (e) {
      setError(e)
    } finally {
      setBusy(false)
    }
  }

  const activeScenario = scenarios.data?.find((x) => x.id === scenario) || null
  const activeTarget = activeScenario?.targets?.find((t) => t.id === target) || null

  return (
    <>
      <PageHeader
        title="Failure Prediction"
        subtitle="Static graph analysis: trace reverse dependencies from a failing component without executing code. Validate predictions in the What-If Lab."
        actions={
          <div className="flex items-center gap-2">
            <Badge className="bg-accent/15 text-accent">Static analysis</Badge>
            <Link to="/lab">
              <Button size="sm" variant="ghost" icon={Beaker}>What-If Lab</Button>
            </Link>
          </div>
        }
      />
      <div className="grid gap-4 lg:grid-cols-[410px_1fr]">
        <div className="space-y-4">
          <Card>
            <CardHeader
              title="Failure scenario"
              subtitle="Choose a supported failure primitive and target component"
              icon={Radar}
            />
            <div className="p-4">
              {scenarios.loading ? <Skeleton className="h-48" /> : scenarios.error ? <ErrorState error={scenarios.error} onRetry={scenarios.reload} /> : (
                <ScenarioPicker scenarios={scenarios.data} scenario={scenario} target={target} onScenario={(s) => { setScenario(s); setTarget('') }} onTarget={setTarget} />
              )}
              <Button className="mt-4 w-full" variant="primary" icon={Radar} loading={busy} disabled={!target} onClick={compute}>
                Generate impact analysis
              </Button>
              {error && <div className="mt-3"><ErrorState error={error} /></div>}
            </div>
          </Card>

          <Card>
            <CardHeader
              title="Previous predictions"
              subtitle={`${history.data?.length || 0} saved static impact runs`}
              icon={History}
            />
            <ul className="max-h-80 divide-y divide-rule overflow-y-auto">
              {(history.data || []).map((p) => {
                const definite = p.impact?.summary?.definite ?? 0
                const possible = p.impact?.summary?.possible ?? 0
                const totalAffected = definite + possible
                const routesAffected = p.impact?.summary?.routes_affected ?? 0
                const isSelected = prediction?.id === p.id
                return (
                  <li key={p.id}>
                    <button
                      type="button"
                      onClick={() => { setPrediction(p); setParams({ id: p.id }) }}
                      className={`w-full px-4 py-3 text-left transition-colors hover:bg-raised ${isSelected ? 'bg-accent/10' : ''}`}
                    >
                      <div className="flex items-center justify-between gap-2">
                        <span className="truncate text-sm font-medium text-ink">
                          {p.impact.scenario_label}: {shortId(p.target_id)}
                        </span>
                        <Badge className={p.ai_explanation ? 'bg-accent/15 text-accent' : 'bg-warning-bg text-warning-ink'}>
                          {p.ai_explanation ? 'AI explained' : 'Static only'}
                        </Badge>
                      </div>
                      <div className="num mt-1 flex flex-wrap items-center gap-x-2 gap-y-0.5 text-xs text-faint">
                        <span>{totalAffected} affected ({definite} definite)</span>
                        <span>·</span>
                        <span>{routesAffected} routes</span>
                        <span>·</span>
                        <span>{timeAgo(p.created_at)}</span>
                      </div>
                    </button>
                  </li>
                )
              })}
              {history.data?.length === 0 && <li className="p-4 text-sm text-faint">No predictions generated yet.</li>}
            </ul>
          </Card>
        </div>

        <div className="min-w-0 space-y-4">
          {!prediction ? (
            <PredictionPreviewPanel
              project={project}
              scenario={activeScenario}
              target={activeTarget}
              busy={busy}
              onCompute={compute}
            />
          ) : (
            <motion.div key={prediction.id} initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} className="space-y-4">
              <Card className="flex flex-wrap items-center justify-between gap-3 p-4">
                <div>
                  <div className="flex items-center gap-2">
                    <Badge className="bg-warning-bg text-warning-ink">Static prediction only · not yet validated</Badge>
                  </div>
                  <div className="mt-1.5 text-base font-semibold text-ink">{prediction.impact.scenario_label} → {prediction.impact.target.name}</div>
                  <div className="mt-0.5 text-xs text-faint">{prediction.impact.fault_description}</div>
                </div>
                <Link to={`/lab?prediction=${prediction.id}`}>
                  <Button variant="cyan" icon={Beaker}>Validate in What-If Lab</Button>
                </Link>
              </Card>

              <div className="space-y-2">
                <div className="flex items-center justify-between px-1">
                  <span className="flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wider text-muted">
                    <Network className="h-3.5 w-3.5 text-accent" /> Deterministic Graph Analysis
                  </span>
                  <span className="text-[11px] text-faint">Computed strictly from AST dependency graph</span>
                </div>
                <ImpactView prediction={prediction} />
              </div>

              <div className="space-y-2">
                <div className="flex items-center justify-between px-1">
                  <span className="flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wider text-muted">
                    <Sparkles className="h-3.5 w-3.5 text-accent" /> AI-Generated Explanation (Optional)
                  </span>
                  <span className="text-[11px] text-faint">Grounded in cited deterministic evidence</span>
                </div>
                <AIPanel
                  key={prediction.id}
                  title="Gemma failure analysis"
                  subtitle="Explains mechanism, assumptions, and mitigations using only cited evidence"
                  purpose="Failure analysis"
                  initial={prediction.ai_explanation}
                  fetcher={(body) => api.explainPrediction(prediction.id, body)}
                  render={(r) => <FailureAnalysisResult r={r} />}
                  onResult={(d) => setPrediction((p) => ({ ...p, ai_explanation: d }))}
                />
              </div>
            </motion.div>
          )}
        </div>
      </div>
    </>
  )
}

function PredictionPreviewPanel({ project, scenario, target, busy, onCompute }) {
  return (
    <Card className="p-6 space-y-6">
      <div className="flex flex-wrap items-start justify-between gap-4 border-b border-rule pb-4">
        <div>
          <div className="flex items-center gap-2 text-xs font-medium text-accent">
            <Radar className="h-4 w-4" /> Static Impact Analysis Preview
          </div>
          <h2 className="mt-1 text-lg font-semibold text-ink">
            {scenario && target ? `${scenario.label} → ${target.name}` : 'Select a scenario and target to trace blast radius'}
          </h2>
          <p className="mt-1 max-w-2xl text-sm text-muted">
            DevDNA traverses the dependency graph backwards from the failing component to classify definitely affected components, handled error boundaries, exposed routes, and covering tests — without executing repository code.
          </p>
        </div>
        <Button variant="primary" icon={Radar} loading={busy} disabled={!target} onClick={onCompute}>
          Generate impact analysis
        </Button>
      </div>

      {/* Visual propagation pipeline preview */}
      <div>
        <div className="mb-2.5 text-xs font-medium uppercase tracking-wider text-faint">How static propagation works</div>
        <div className="grid gap-2.5 sm:grid-cols-4">
          {[
            { step: '1. Failing Target', value: target ? shortId(target.id) : 'Select target', detail: target ? `Criticality ${target.criticality ?? 0}/100` : 'Discovered resource' },
            { step: '2. Reverse BFS', value: target ? `${target.dependents ?? 0} dependents` : '—', detail: 'Traces call & data edges' },
            { step: '3. Route Exposure', value: target?.routes_total ? `${target.routes_depending ?? 0}/${target.routes_total} routes` : 'Mapped routes', detail: 'Flags try/except guards' },
            { step: '4. Test Selection', value: typeof target?.tests_covering === 'number' ? `${target.tests_covering} tests` : 'Covering tests', detail: 'Ready for What-If Lab' },
          ].map((node) => (
            <div key={node.step} className="rounded-card border border-edge bg-ground p-3">
              <div className="text-[11px] font-medium text-faint">{node.step}</div>
              <div className="num mt-1 truncate text-sm font-semibold text-ink">{node.value}</div>
              <div className="mt-0.5 text-[11px] text-muted">{node.detail}</div>
            </div>
          ))}
        </div>
      </div>

      {/* Deterministic vs AI vs Lab distinction */}
      <div className="grid gap-3 md:grid-cols-2">
        <div className="rounded-card border border-edge bg-ground p-3.5 text-xs">
          <div className="flex items-center gap-1.5 font-medium text-ink">
            <CheckCircle2 className="h-3.5 w-3.5 text-positive" /> Deterministic Graph Engine
          </div>
          <p className="mt-1 leading-relaxed text-muted">
            Affected components, hop distances, route exposure, and heuristic risk scores (0–100) are calculated deterministically from the AST graph with zero AI guesswork.
          </p>
        </div>
        <div className="rounded-card border border-edge bg-ground p-3.5 text-xs">
          <div className="flex items-center gap-1.5 font-medium text-ink">
            <Sparkles className="h-3.5 w-3.5 text-accent" /> Optional AI & Controlled Validation
          </div>
          <p className="mt-1 leading-relaxed text-muted">
            After generating impact, optionally ask Gemma to explain the cited evidence, or send the prediction to the{' '}
            <Link to="/lab" className="text-accent hover:underline">What-If Lab</Link> to run a controlled fault-injection experiment.
          </p>
        </div>
      </div>

      <div className="flex flex-wrap items-center justify-between gap-2 border-t border-rule pt-4 text-xs text-faint">
        <span>Active project: <span className="font-medium text-muted">{project.name}</span></span>
        <Link to="/architecture" className="flex items-center gap-1 text-accent hover:underline">
          Inspect full dependency graph <ArrowRight className="h-3.5 w-3.5" />
        </Link>
      </div>
    </Card>
  )
}
