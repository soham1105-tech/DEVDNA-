import { motion } from 'framer-motion'
import { ArrowLeft, ArrowRight, Ban, Beaker, Check, Container, Lock, Play, ShieldCheck, Sparkles, Timer } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import AIPanel from '../components/AIPanel'
import { FailureAnalysisResult } from '../components/AIRenderers'
import CompletionBurst from '../components/CompletionBurst'
import ImpactView from '../components/ImpactView'
import PredictionVsReality from '../components/PredictionVsReality'
import ProjectGate from '../components/ProjectGate'
import ScenarioPicker from '../components/ScenarioPicker'
import { Button, Card, ErrorState, InlineNotice, PageHeader, Skeleton } from '../components/ui'
import { useAsync } from '../hooks/useAsync'
import { useStatus } from '../hooks/useStatus'
import { api } from '../services/api'

const STEPS = ['Scenario', 'Impact', 'Explain', 'Approve', 'Execute', 'Compare']
const DONE = ['prediction_confirmed', 'partially_confirmed', 'prediction_contradicted', 'inconclusive', 'blocked', 'failed']

export default function WhatIfLab() {
  return <ProjectGate>{(project) => <Lab project={project} />}</ProjectGate>
}

function Stepper({ step, maxStep, onStep }) {
  return (
    <ol className="mb-6 grid grid-cols-6 gap-1">
      {STEPS.map((s, i) => {
        const done = i < step
        const active = i === step
        const reachable = i <= maxStep && i < 4
        return (
          <li key={s}>
            <button disabled={!reachable} onClick={() => onStep(i)} className="group w-full text-left disabled:cursor-default" aria-current={active ? 'step' : undefined}>
              {/* Progress track: the accent marks position, never fills the step. */}
              <div className="h-px overflow-hidden bg-control">
                <motion.div className="h-full bg-accent" initial={false} animate={{ width: done ? '100%' : active ? '50%' : '0%' }} transition={{ duration: 0.35 }} />
              </div>
              <div className="mt-2 flex items-center gap-1.5">
                <span className={`num flex h-5 w-5 shrink-0 items-center justify-center rounded-full border text-[11px] ${done ? 'border-positive/50 text-positive' : active ? 'border-accent text-accent' : 'border-control text-faint'}`}>
                  {done ? <Check className="h-3 w-3" /> : i + 1}
                </span>
                <span className={`hidden truncate text-xs sm:block ${active ? 'text-ink' : 'text-faint'}`}>{s}</span>
              </div>
            </button>
          </li>
        )
      })}
    </ol>
  )
}

function Lab({ project }) {
  const [params, setParams] = useSearchParams()
  const status = useStatus()
  const scenarios = useAsync(() => api.scenarios(project.id), [project.id, project.latest_run_id])
  const [step, setStep] = useState(0)
  const [maxStep, setMaxStep] = useState(0)
  const [scenario, setScenario] = useState('db_unavailable')
  const [target, setTarget] = useState('')
  const [prediction, setPrediction] = useState(null)
  const [experiment, setExperiment] = useState(null)
  const [approved, setApproved] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)
  const [elapsed, setElapsed] = useState(0)
  const [scenarioMode, setScenarioMode] = useState('preset') // 'preset' | 'dynamic'
  const [customPrompt, setCustomPrompt] = useState('')
  const [dynamicConsent, setDynamicConsent] = useState(false)
  const [synthesizing, setSynthesizing] = useState(false)
  const startedAt = useRef(null)

  const go = (n) => {
    setStep(n)
    setMaxStep((m) => Math.max(m, n))
  }

  useEffect(() => {
    const s = scenarios.data?.find((x) => x.id === scenario)
    if (s && !s.targets.some((t) => t.id === target)) setTarget(s.targets[0]?.id || '')
  }, [scenarios.data, scenario, target])

  useEffect(() => {
    const sc = params.get('scenario')
    if (sc && ['db_unavailable', 'external_api_unavailable', 'missing_env_var', 'worker_unavailable'].includes(sc)) {
      setScenario(sc)
    }
    const pid = params.get('prediction')
    if (pid && pid !== prediction?.id) {
      api.prediction(pid).then((p) => {
        setPrediction(p)
        setScenario(p.scenario)
        setTarget(p.target_id)
        go(1)
      }).catch(setError)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [params])

  // Poll the running experiment until it reaches a final status.
  useEffect(() => {
    if (!experiment || DONE.includes(experiment.status)) return undefined
    const t = setInterval(async () => {
      setElapsed(Math.round((Date.now() - startedAt.current) / 1000))
      try {
        const e = await api.experiment(experiment.id)
        if (DONE.includes(e.status)) {
          setExperiment(e)
          go(5)
        } else setExperiment((x) => ({ ...x, status: e.status }))
      } catch (err) {
        setError(err)
      }
    }, 800)
    return () => clearInterval(t)
  }, [experiment])

  const createPrediction = async () => {
    setBusy(true)
    setError(null)
    try {
      const p = await api.createPrediction(project.id, scenario, target)
      setPrediction(p)
      setExperiment(null)
      setApproved(false)
      setParams({ prediction: p.id })
      go(1)
    } catch (e) {
      setError(e)
    } finally {
      setBusy(false)
    }
  }

  const synthesizeAndPredict = async () => {
    if (!customPrompt.trim()) return
    setSynthesizing(true)
    setError(null)
    try {
      const syn = await api.dynamicScenario(project.id, { prompt: customPrompt, consent: true })
      const spec = syn.result
      const p = await api.createPrediction(
        project.id,
        spec.scenario_id,
        spec.suggested_target_id,
        spec
      )
      setPrediction(p)
      setScenario(spec.scenario_id)
      setTarget(spec.suggested_target_id)
      setExperiment(null)
      setApproved(false)
      setParams({ prediction: p.id })
      go(1)
    } catch (e) {
      setError(e)
    } finally {
      setSynthesizing(false)
    }
  }

  const run = async () => {
    setBusy(true)
    setError(null)
    try {
      startedAt.current = Date.now()
      setElapsed(0)
      const e = await api.startExperiment(prediction.id)
      setExperiment(e)
      go(4)
    } catch (e) {
      setError(e)
    } finally {
      setBusy(false)
    }
  }

  const restart = () => {
    setPrediction(null)
    setExperiment(null)
    setApproved(false)
    setParams({})
    setStep(0)
    setMaxStep(0)
  }

  const docker = status?.ai?.docker_sandbox
  const mode = project.trusted ? 'local' : docker ? 'docker' : 'blocked'

  return (
    <>
      <PageHeader
        title="Predict, approve, execute, compare"
        subtitle="Run a supported failure scenario against the project's own test suite in a controlled environment, then compare reality with the prediction."
        actions={step > 0 && <Button variant="ghost" onClick={restart}>New scenario</Button>}
      />
      <Stepper step={step} maxStep={maxStep} onStep={setStep} />
      {error && <div className="mb-4"><ErrorState error={error} /></div>}

      <motion.div key={step} initial={{ opacity: 0, x: 16 }} animate={{ opacity: 1, x: 0 }} transition={{ duration: 0.22 }}>
          {step === 0 && (
            <Card className="mx-auto max-w-3xl p-5 space-y-4">
              <div className="flex flex-wrap items-center justify-between gap-2 border-b border-rule pb-3">
                <div>
                  <h2 className="text-base font-semibold text-ink">1. Select or Synthesize Failure Scenario</h2>
                  <p className="mt-0.5 text-xs text-muted">Target a standard chaos primitive or let Gemma 4 synthesize an arbitrary failure.</p>
                </div>
                <div className="flex rounded-control border border-edge bg-surface p-0.5 text-xs font-medium">
                  <button
                    onClick={() => { setScenarioMode('preset'); setDynamicConsent(false) }}
                    className={`rounded-full px-3 py-1.5 transition-colors ${scenarioMode === 'preset' ? 'bg-raised text-ink' : 'text-faint hover:text-muted'}`}
                  >
                    Standard Primitives
                  </button>
                  <button
                    onClick={() => { setScenarioMode('dynamic'); setDynamicConsent(false) }}
                    className={`flex items-center gap-1.5 rounded-full px-3 py-1.5 transition-colors ${scenarioMode === 'dynamic' ? 'bg-accent/15 text-accent' : 'text-faint hover:text-muted'}`}
                  >
                    <Sparkles className="h-3 w-3" />
                    Gemma 4 Dynamic Synthesizer
                  </button>
                </div>
              </div>

              {scenarioMode === 'preset' ? (
                <>
                  {scenarios.loading ? <Skeleton className="h-48" /> : scenarios.data && (
                    <ScenarioPicker scenarios={scenarios.data} scenario={scenario} target={target} onScenario={(s) => { setScenario(s); setTarget('') }} onTarget={setTarget} />
                  )}
                  <div className="flex justify-end pt-2">
                    <Button variant="primary" icon={ArrowRight} loading={busy} disabled={!target} onClick={createPrediction}>Generate impact analysis</Button>
                  </div>
                </>
              ) : (
                <div className="space-y-3.5">
                  <div className="rounded-card border border-accent/30 bg-accent/10 space-y-2 p-3.5">
                    <div className="flex items-center gap-2 text-xs font-medium text-accent">
                      <Sparkles className="h-3.5 w-3.5" /> Arbitrary Chaos Scenario Generation
                    </div>
                    <p className="text-xs leading-relaxed text-muted">
                      Describe any arbitrary failure mode in natural language. Gemma 4 will inspect the repository AST, map the failure to vulnerable target components, identify relevant exception hierarchies, and generate a dynamic chaos fault specification.
                    </p>
                  </div>

                  <div>
                    <label className="mb-1.5 block text-xs font-medium text-muted" htmlFor="dynamic-prompt">
                      Describe the Failure Scenario to Simulate:
                    </label>
                    <textarea
                      id="dynamic-prompt"
                      rows={3}
                      value={customPrompt}
                      onChange={(e) => { setCustomPrompt(e.target.value); setDynamicConsent(false) }}
                      placeholder="e.g. Simulate Redis cache eviction storm and socket ConnectionResetError during checkout surge..."
                      className="w-full resize-none rounded-card border border-control bg-surface p-3 text-sm text-ink outline-none focus:border-accent"
                    />
                  </div>

                  <div className="flex flex-wrap gap-1.5 items-center">
                    <span className="mr-1 text-[11px] font-medium text-faint">Quick prompts:</span>
                    {[
                      'Redis connection drop & cache eviction',
                      'JWT auth token expired cascade',
                      'Payment gateway 504 Gateway Timeout',
                      'Database connection pool exhaustion',
                    ].map((p, i) => (
                      <button
                        key={i}
                        type="button"
                        onClick={() => { setCustomPrompt(p); setDynamicConsent(false) }}
                        className="rounded-full border border-edge bg-ground px-2 py-1 text-[11px] text-muted transition-colors hover:bg-raised hover:text-ink"
                      >
                        {p}
                      </button>
                    ))}
                  </div>

                  <label className="flex items-start gap-2 text-xs text-muted">
                    <input
                      type="checkbox"
                      checked={dynamicConsent}
                      onChange={(e) => setDynamicConsent(e.target.checked)}
                      className="mt-0.5 accent-[var(--color-accent)]"
                    />
                    <span>I understand that my prompt and repository evidence will be sent to Gemma.</span>
                  </label>

                  <div className="flex justify-end pt-2">
                    <Button
                      variant="primary"
                      icon={Sparkles}
                      loading={synthesizing}
                      disabled={!customPrompt.trim() || !dynamicConsent}
                      onClick={synthesizeAndPredict}
                    >
                      Synthesize with Gemma 4 & Simulate
                    </Button>
                  </div>
                </div>
              )}
            </Card>
          )}

          {step === 1 && prediction && (
            <div className="space-y-4">
              <InlineNotice tone="violet" icon={Beaker}>
                <span className="font-medium">Prediction only.</span> {prediction.impact.scenario_label} on {prediction.impact.target.name}: computed from the graph, not yet tested.
              </InlineNotice>
              <ImpactView prediction={prediction} compact />
              <Nav onBack={() => setStep(0)} onNext={() => go(2)} nextLabel="Explain with Gemma" />
            </div>
          )}

          {step === 2 && prediction && (
            <div className="space-y-4">
              <AIPanel
                key={prediction.id}
                title="Gemma failure analysis"
                subtitle="Optional: explains the predicted failure from cited evidence"
                purpose="Failure analysis"
                initial={prediction.ai_explanation}
                fetcher={(body) => api.explainPrediction(prediction.id, body)}
                render={(r) => <FailureAnalysisResult r={r} />}
                onResult={(d) => setPrediction((p) => ({ ...p, ai_explanation: d }))}
              />
              <Nav onBack={() => setStep(1)} onNext={() => go(3)} nextLabel={prediction.ai_explanation ? 'Continue to approval' : 'Skip to approval'} />
            </div>
          )}

          {step === 3 && prediction && (
            <Card className="mx-auto max-w-3xl p-5">
              <h2 className="flex items-center gap-2 text-base font-semibold text-ink"><ShieldCheck className="h-4 w-4 text-accent" /> 4. Approve the experiment</h2>
              <p className="mt-1 text-sm text-muted">Nothing runs without your approval. This is exactly what will happen:</p>
              <ul className="mt-4 space-y-2 text-sm text-muted">
                <li className="flex gap-2"><Container className="mt-0.5 h-4 w-4 shrink-0 text-faint" />
                  {mode === 'local' && 'Mode: local restricted harness (allowed because this is the bundled, trusted sample).'}
                  {mode === 'docker' && 'Mode: Docker sandbox: no network, read-only source, 512 MB memory, 1 CPU, 128 processes.'}
                  {mode === 'blocked' && 'Mode: BLOCKED. This project is untrusted and the Docker sandbox is unavailable, so no code will run.'}
                </li>
                <li className="flex gap-2"><Play className="mt-0.5 h-4 w-4 shrink-0 text-faint" /> A disposable copy of the project runs <code className="num whitespace-nowrap text-xs text-ink">python -m pytest</code> twice: once as a baseline, once with the fault injected.</li>
                <li className="flex gap-2"><Beaker className="mt-0.5 h-4 w-4 shrink-0 text-faint" /> Fault: {prediction.impact.fault_description}</li>
                <li className="flex gap-2"><Lock className="mt-0.5 h-4 w-4 shrink-0 text-faint" /> Outbound network blocked, environment scrubbed of API keys, {90}s timeout, output capped, temp files deleted afterwards.</li>
                <li className="flex gap-2"><Timer className="mt-0.5 h-4 w-4 shrink-0 text-faint" /> No production systems are touched. The fixed command cannot be changed from the UI.</li>
              </ul>
              {mode === 'blocked' && (
                <div className="mt-4"><InlineNotice tone="warn" icon={Ban}>If you continue, the experiment is recorded as <b>Blocked</b> and the prediction stays unvalidated.</InlineNotice></div>
              )}
              <label className="mt-5 flex cursor-pointer items-start gap-2.5 text-sm text-ink">
                <input type="checkbox" className="mt-0.5 h-4 w-4 accent-[var(--color-accent)]" checked={approved} onChange={(e) => setApproved(e.target.checked)} />
                I approve running this experiment as described.
              </label>
              <div className="mt-5 flex justify-between">
                <Button variant="ghost" icon={ArrowLeft} onClick={() => setStep(2)}>Back</Button>
                <Button variant="cyan" icon={Play} disabled={!approved} loading={busy} onClick={run}>Approve and run</Button>
              </div>
            </Card>
          )}

          {step === 4 && experiment && (
            <Card className="mx-auto max-w-xl p-8 text-center">
              <div className="relative mx-auto h-20 w-20">
                <motion.div className="absolute inset-0 rounded-full border border-control border-t-accent" animate={{ rotate: 360 }} transition={{ repeat: Infinity, duration: 1.1, ease: 'linear' }} />
                <Beaker className="absolute inset-0 m-auto h-8 w-8 text-muted" />
              </div>
              <h2 className="mt-5 text-base font-semibold text-ink">Running experiment…</h2>
              <p className="num mt-1 text-sm text-muted">Status: {experiment.status} · {elapsed}s elapsed</p>
              <p className="mt-3 text-xs text-faint">Baseline run, then faulted run. Results appear as soon as both finish.</p>
            </Card>
          )}

          {step === 5 && experiment && (
            <div className="space-y-4">
              {experiment.status !== 'blocked' && experiment.status !== 'failed' && <CompletionBurst status={experiment.status} />}
              <PredictionVsReality experiment={experiment} onUpdate={setExperiment} />
              <div className="text-center text-sm text-faint">
                Saved. <Link to={`/experiments/${experiment.id}`} className="text-accent hover:underline">Open in Experiments</Link>
              </div>
            </div>
          )}
      </motion.div>
    </>
  )
}

function Nav({ onBack, onNext, nextLabel }) {
  return (
    <div className="flex justify-between">
      <Button variant="ghost" icon={ArrowLeft} onClick={onBack}>Back</Button>
      <Button variant="primary" onClick={onNext}>{nextLabel} <ArrowRight className="h-4 w-4" /></Button>
    </div>
  )
}
