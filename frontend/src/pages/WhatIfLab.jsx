import { motion } from 'framer-motion'
import { ArrowLeft, ArrowRight, Ban, Beaker, Check, Container, Lock, Play, Radar, ShieldCheck, Sparkles, Timer } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import AIPanel from '../components/AIPanel'
import { FailureAnalysisResult } from '../components/AIRenderers'
import CompletionBurst from '../components/CompletionBurst'
import ExplainAnalysisButton from '../components/ExplainAnalysisButton'
import ImpactView from '../components/ImpactView'
import PredictionVsReality from '../components/PredictionVsReality'
import ProjectGate from '../components/ProjectGate'
import ScenarioPicker from '../components/ScenarioPicker'
import TermExplainer from '../components/TermExplainer'
import { Badge, Button, Card, ErrorState, InlineNotice, PageHeader, Skeleton } from '../components/ui'
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
    <ol className="mb-6 grid grid-cols-6 gap-1.5">
      {STEPS.map((s, i) => {
        const done = i < step
        const active = i === step
        const reachable = i <= maxStep && i < 4
        return (
          <li key={s}>
            <button disabled={!reachable} onClick={() => onStep(i)} className="group w-full text-left disabled:cursor-default" aria-current={active ? 'step' : undefined}>
              <div className="h-px overflow-hidden bg-control">
                <motion.div className="h-full bg-accent" initial={false} animate={{ width: done ? '100%' : active ? '50%' : '0%' }} transition={{ duration: 0.35 }} />
              </div>
              <div className="mt-2 flex items-center gap-1.5">
                <span className={`num flex h-5 w-5 shrink-0 items-center justify-center rounded-full border text-[11px] ${done ? 'border-positive/50 text-positive' : active ? 'border-accent text-accent' : 'border-control text-faint'}`}>
                  {done ? <Check className="h-3 w-3" /> : i + 1}
                </span>
                <span className={`hidden truncate text-xs sm:block ${active ? 'font-medium text-ink' : 'text-faint'}`}>{s}</span>
              </div>
            </button>
          </li>
        )
      })}
    </ol>
  )
}

function ExecutionEnvironmentCard({ mode, project, experimentsDisabled }) {
  const modeMeta = {
    local: {
      badge: 'Local restricted harness',
      badgeCls: 'bg-positive-bg text-positive-ink',
      detail: 'Permitted because this is the bundled, trusted sample project.',
    },
    docker: {
      badge: 'Docker sandbox',
      badgeCls: 'bg-accent/15 text-accent',
      detail: 'Untrusted code runs in an isolated container: no network, read-only source, 512 MB RAM, 1 CPU, 128 PIDs.',
    },
    blocked: {
      badge: 'Blocked · Static only',
      badgeCls: 'bg-warning-bg text-warning-ink',
      detail: experimentsDisabled
        ? 'Experiment execution is disabled on this server (DEVDNA_EXPERIMENTS_ENABLED=false). No untrusted code will execute.'
        : project.analysis_only
        ? 'Untrusted JavaScript/TypeScript projects require a Docker JS sandbox, which is not available in this build.'
        : 'This project is untrusted and the Docker sandbox is unavailable, so no code will execute.',
    },
  }[mode]

  return (
    <Card className="p-4 space-y-3">
      <div className="flex items-center justify-between gap-2">
        <span className="flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wider text-muted">
          <ShieldCheck className="h-3.5 w-3.5 text-accent" /> Execution Environment
        </span>
        <Badge className={modeMeta.badgeCls}>{modeMeta.badge}</Badge>
      </div>
      <p className="text-xs leading-relaxed text-muted">{modeMeta.detail}</p>
      <ul className="space-y-1.5 border-t border-rule pt-2.5 text-[11px] text-faint">
        <li className="flex items-start gap-2"><Container className="mt-0.5 h-3.5 w-3.5 shrink-0" /> Fixed test runner only (<code className="num text-muted">python -m pytest</code> or <code className="num text-muted">npx jest --ci --json</code>).</li>
        <li className="flex items-start gap-2"><Lock className="mt-0.5 h-3.5 w-3.5 shrink-0" /> Environment scrubbed of secrets, outbound network blocked, 90s hard timeout.</li>
        <li className="flex items-start gap-2"><Timer className="mt-0.5 h-3.5 w-3.5 shrink-0" /> Runs on a disposable copy; temporary files cleaned up immediately.</li>
      </ul>
    </Card>
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
      const syn = await api.dynamicScenario(project.id, { prompt: customPrompt, consent: dynamicConsent })
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
  const experimentsDisabled = status?.ai?.experiments_enabled === false
  const mode = experimentsDisabled ? 'blocked' : project.trusted ? 'local' : project.analysis_only ? 'blocked' : docker ? 'docker' : 'blocked'

  return (
    <>
      <PageHeader
        title="What-If Lab"
        subtitle="Controlled experiment validation: predict impact, review isolation guarantees, approve execution, and compare observed test outcomes with the static prediction."
        actions={
          <div className="flex flex-wrap items-center gap-2">
            <Badge className="bg-positive-bg text-positive-ink">Controlled validation</Badge>
            <ExplainAnalysisButton kind="what_if_lab" data={{ mode, prediction, experiment, step }} />
            <Link to="/prediction">
              <Button size="sm" variant="ghost" icon={Radar}>Static Prediction</Button>
            </Link>
            {step > 0 && <Button size="sm" variant="ghost" onClick={restart}>New scenario</Button>}
          </div>
        }
      />
      <Stepper step={step} maxStep={maxStep} onStep={setStep} />
      {error && <div className="mb-4"><ErrorState error={error} /></div>}

      <motion.div key={step} initial={{ opacity: 0, x: 16 }} animate={{ opacity: 1, x: 0 }} transition={{ duration: 0.22 }}>
          {step === 0 && (
            <div className="grid gap-4 lg:grid-cols-[1fr_340px] items-start">
              <Card className="p-5 space-y-4">
                <div className="flex flex-wrap items-center justify-between gap-3 border-b border-rule pb-3">
                  <div>
                    <h2 className="text-base font-semibold text-ink">1. Select or Synthesize Failure Scenario</h2>
                    <p className="mt-0.5 text-xs text-muted">Choose a deterministic fault primitive or synthesize a custom failure scenario with Gemma 4.</p>
                  </div>
                  <div className="flex rounded-control border border-edge bg-surface p-0.5 text-xs font-medium">
                    <button
                      type="button"
                      onClick={() => { setScenarioMode('preset'); setDynamicConsent(false) }}
                      className={`rounded-full px-3 py-1.5 transition-colors ${scenarioMode === 'preset' ? 'bg-raised text-ink' : 'text-faint hover:text-muted'}`}
                    >
                      Standard Primitives
                    </button>
                    <button
                      type="button"
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
                    {scenarios.loading ? <Skeleton className="h-48" /> : scenarios.error ? (
                      <ErrorState error={scenarios.error} onRetry={scenarios.reload} />
                    ) : scenarios.data && (
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
                        <Sparkles className="h-3.5 w-3.5" /> AI-Generated Failure Scenario (Gemma 4)
                      </div>
                      <p className="text-xs leading-relaxed text-muted">
                        Describe a failure mode in natural language. Gemma 4 inspects discovered repository evidence to propose a target component and exception specification, then the deterministic graph engine computes the impact.
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
                          className="rounded-full border border-edge bg-ground px-2.5 py-1 text-[11px] text-muted transition-colors hover:bg-raised hover:text-ink"
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

              <div className="space-y-4">
                <ExecutionEnvironmentCard mode={mode} project={project} experimentsDisabled={experimentsDisabled} />
                <Card className="p-4 space-y-2.5 text-xs">
                  <div className="font-semibold text-ink">Standard Primitives vs. AI Scenarios</div>
                  <div>
                    <span className="font-medium text-ink">Standard Primitives:</span>{' '}
                    <span className="text-muted">Built-in fault injectors (DB, HTTP, env var, worker) mapped directly to discovered AST resources. Work offline with no API key.</span>
                  </div>
                  <div className="border-t border-rule pt-2">
                    <span className="font-medium text-accent">AI-Generated Scenarios:</span>{' '}
                    <span className="text-muted">Gemma 4 translates custom failure descriptions into a structured fault specification after explicit consent.</span>
                  </div>
                </Card>
              </div>
            </div>
          )}

          {step === 1 && prediction && (
            <div className="space-y-4">
              <InlineNotice tone="violet" icon={Beaker}>
                <span className="font-medium">Static prediction only (unvalidated).</span> {prediction.impact.scenario_label} on {prediction.impact.target.name}: computed deterministically from the dependency graph, not yet executed.
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
            <div className="grid gap-4 lg:grid-cols-[1fr_340px] items-start">
              <Card className="p-5">
                <h2 className="flex items-center gap-2 text-base font-semibold text-ink">
                  <ShieldCheck className="h-4 w-4 text-accent" />
                  <TermExplainer termId="fault_injection">4. Approve the experiment</TermExplainer>
                </h2>
                <p className="mt-1 text-sm text-muted">Nothing runs without your explicit approval. Review the exact scope and safety restrictions below:</p>
                <ul className="mt-4 space-y-2.5 text-sm text-muted">
                  <li className="flex gap-2.5"><Container className="mt-0.5 h-4 w-4 shrink-0 text-faint" />
                    <span>
                      {mode === 'local' && 'Execution mode: local restricted harness (permitted because this is the bundled, trusted sample project).'}
                      {mode === 'docker' && 'Execution mode: Docker sandbox (no network, read-only source, 512 MB memory, 1 CPU, 128 processes).'}
                      {mode === 'blocked' && (experimentsDisabled
                        ? 'Execution mode: BLOCKED. Experiment execution is disabled by server configuration (DEVDNA_EXPERIMENTS_ENABLED=false), so no code will execute.'
                        : project.analysis_only
                        ? 'Execution mode: BLOCKED. Untrusted JavaScript/TypeScript projects require a Docker JavaScript sandbox, so no code will execute.'
                        : 'Execution mode: BLOCKED. This project is untrusted and the Docker sandbox is unavailable, so no code will execute.')}
                    </span>
                  </li>
                  <li className="flex gap-2.5"><Play className="mt-0.5 h-4 w-4 shrink-0 text-faint" /> <span>A disposable copy of the project runs the fixed test command (<code className="num whitespace-nowrap text-xs text-ink">python -m pytest</code> for Python or <code className="num whitespace-nowrap text-xs text-ink">npx jest --ci --json</code> for JavaScript) twice: once as a baseline, once with the fault injected.</span></li>
                  <li className="flex gap-2.5"><Beaker className="mt-0.5 h-4 w-4 shrink-0 text-faint" /> <span>Injected fault: {prediction.impact.fault_description}</span></li>
                  <li className="flex gap-2.5"><Lock className="mt-0.5 h-4 w-4 shrink-0 text-faint" /> <span>Outbound network blocked, environment scrubbed of API keys and secrets, 90s timeout, output size capped, temporary directory removed afterwards.</span></li>
                  <li className="flex gap-2.5"><Timer className="mt-0.5 h-4 w-4 shrink-0 text-faint" /> <span>No production systems or original project files are modified. Arbitrary shell commands are never accepted.</span></li>
                </ul>
                {mode === 'blocked' && (
                  <div className="mt-4">
                    <InlineNotice tone="warn" icon={Ban}>
                      <b>No code will execute.</b> Continuing records an audit entry with status <b>Blocked</b> and leaves the prediction unvalidated.
                    </InlineNotice>
                  </div>
                )}
                <label className="mt-5 flex cursor-pointer items-start gap-2.5 text-sm text-ink">
                  <input type="checkbox" className="mt-0.5 h-4 w-4 accent-[var(--color-accent)]" checked={approved} onChange={(e) => setApproved(e.target.checked)} />
                  {mode === 'blocked'
                    ? 'I understand this experiment is blocked and no project code will execute.'
                    : 'I approve running this controlled experiment as described.'}
                </label>
                <div className="mt-5 flex justify-between">
                  <Button variant="ghost" icon={ArrowLeft} onClick={() => setStep(2)}>Back</Button>
                  <Button variant={mode === 'blocked' ? 'ghost' : 'cyan'} icon={mode === 'blocked' ? Ban : Play} disabled={!approved} loading={busy} onClick={run}>
                    {mode === 'blocked' ? 'Record as blocked (no code runs)' : 'Approve and run'}
                  </Button>
                </div>
              </Card>

              <ExecutionEnvironmentCard mode={mode} project={project} experimentsDisabled={experimentsDisabled} />
            </div>
          )}

          {step === 4 && experiment && (
            <Card className="mx-auto max-w-xl p-8 text-center">
              <div className="relative mx-auto h-20 w-20">
                <motion.div className="absolute inset-0 rounded-full border border-control border-t-accent" animate={{ rotate: 360 }} transition={{ repeat: Infinity, duration: 1.1, ease: 'linear' }} />
                <Beaker className="absolute inset-0 m-auto h-8 w-8 text-muted" />
              </div>
              <h2 className="mt-5 text-base font-semibold text-ink">
                {mode === 'blocked' ? 'Recording blocked experiment…' : 'Executing controlled experiment…'}
              </h2>
              <p className="num mt-1 text-sm text-muted">Status: {experiment.status} · {elapsed}s elapsed</p>
              <p className="mt-3 text-xs text-faint">
                {mode === 'blocked'
                  ? 'No project code is being executed; recording audit state.'
                  : 'Running baseline suite, then fault-injected suite. Results appear as soon as both finish.'}
              </p>
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
