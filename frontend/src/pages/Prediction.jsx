import { motion } from 'framer-motion'
import { Beaker, History, Radar } from 'lucide-react'
import { useEffect, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import AIPanel from '../components/AIPanel'
import { FailureAnalysisResult } from '../components/AIRenderers'
import ImpactView from '../components/ImpactView'
import ProjectGate from '../components/ProjectGate'
import ScenarioPicker from '../components/ScenarioPicker'
import { Button, Card, CardHeader, EmptyState, ErrorState, PageHeader, Skeleton } from '../components/ui'
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

  return (
    <>
      <PageHeader
        title="What breaks if this fails?"
        subtitle="Affected components are computed deterministically from the graph. Gemma then explains the mechanism, assumptions and mitigations from cited evidence."
      />
      <div className="grid gap-4 lg:grid-cols-[380px_1fr]">
        <div className="space-y-4">
          <Card>
            <CardHeader title="Scenario" icon={Radar} />
            <div className="p-4">
              {scenarios.loading ? <Skeleton className="h-48" /> : scenarios.error ? <ErrorState error={scenarios.error} onRetry={scenarios.reload} /> : (
                <ScenarioPicker scenarios={scenarios.data} scenario={scenario} target={target} onScenario={(s) => { setScenario(s); setTarget('') }} onTarget={setTarget} />
              )}
              <Button className="mt-4 w-full" variant="primary" icon={Radar} loading={busy} disabled={!target} onClick={compute}>
                Compute impact
              </Button>
              {error && <div className="mt-3"><ErrorState error={error} /></div>}
            </div>
          </Card>
          <Card>
            <CardHeader title="Previous predictions" icon={History} />
            <ul className="max-h-72 divide-y divide-rule overflow-y-auto">
              {(history.data || []).map((p) => (
                <li key={p.id}>
                  <button onClick={() => { setPrediction(p); setParams({ id: p.id }) }} className={`w-full px-4 py-2.5 text-left hover:bg-raised ${prediction?.id === p.id ? 'bg-accent/10' : ''}`}>
                    <div className="truncate text-sm text-ink">{p.impact.scenario_label}: {shortId(p.target_id)}</div>
                    <div className="text-xs text-faint">{p.impact.summary.definite} definite · {timeAgo(p.created_at)}{p.ai_explanation ? ' · explained by Gemma' : ''}</div>
                  </button>
                </li>
              ))}
              {history.data?.length === 0 && <li className="p-4 text-sm text-faint">No predictions yet.</li>}
            </ul>
          </Card>
        </div>

        <div className="min-w-0 space-y-4">
          {!prediction ? (
            <EmptyState icon={Radar} title="Pick a scenario and compute its impact" description="DevDNA walks the dependency graph backwards from the failing component and marks every route, function and test that could break." />
          ) : (
            <motion.div key={prediction.id} initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} className="space-y-4">
              <Card className="flex flex-wrap items-center justify-between gap-3 p-4">
                <div>
                  <div className="text-xs text-warning">Prediction only · not yet validated</div>
                  <div className="mt-0.5 text-base font-semibold text-ink">{prediction.impact.scenario_label} → {prediction.impact.target.name}</div>
                  <div className="text-xs text-faint">{prediction.impact.fault_description}</div>
                </div>
                <Link to={`/lab?prediction=${prediction.id}`}>
                  <Button variant="cyan" icon={Beaker}>Validate in What-If Lab</Button>
                </Link>
              </Card>
              <AIPanel
                key={prediction.id}
                title="Gemma failure analysis"
                subtitle="Explains why, using only the cited evidence"
                purpose="Failure analysis"
                initial={prediction.ai_explanation}
                fetcher={(body) => api.explainPrediction(prediction.id, body)}
                render={(r) => <FailureAnalysisResult r={r} />}
                onResult={(d) => setPrediction((p) => ({ ...p, ai_explanation: d }))}
              />
              <ImpactView prediction={prediction} />
            </motion.div>
          )}
        </div>
      </div>
    </>
  )
}
