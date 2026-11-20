import { useState, useEffect } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import {
  GitPullRequest,
  ShieldAlert,
  AlertTriangle,
  CheckCircle2,
  XCircle,
  Play,
  RotateCcw,
  Sparkles,
  Layers,
  Network,
  TestTube2,
  FileCode2,
  BarChart3,
  ExternalLink,
  ChevronRight,
  Info,
  Clock,
  Zap,
  Activity,
  ArrowRight,
  Check,
  Shield,
  Filter,
} from 'lucide-react'
import { useProject } from '../hooks/useProject'
import { api } from '../services/api'
import ExplainAnalysisButton from '../components/ExplainAnalysisButton'
import TermExplainer from '../components/TermExplainer'
import {
  Button,
  Card,
  CardHeader,
  PageHeader,
  Stat,
  StatStrip,
  Badge,
  SeverityBadge,
  Spinner,
  EmptyState,
  ErrorState,
  InlineNotice,
} from '../components/ui'

const TABS = [
  { id: 'overview', label: 'Change Overview', icon: FileCode2 },
  { id: 'impact', label: 'Impact Graph', icon: Network },
  { id: 'risk', label: 'Risk Breakdown', icon: BarChart3 },
  { id: 'tests', label: 'Test Intelligence', icon: TestTube2 },
  { id: 'validation', label: 'Validation Report', icon: Zap },
  { id: 'ai', label: 'AI Explanation', icon: Sparkles },
]

export default function ChangeIntelligence() {
  const { current: project, loading: projectLoading } = useProject()
  const [activeTab, setActiveTab] = useState('overview')
  const [analysis, setAnalysis] = useState(null)
  const [history, setHistory] = useState([])
  const [loading, setLoading] = useState(false)
  const [validating, setValidating] = useState(false)
  const [error, setError] = useState(null)

  // Custom input state
  const [inputMode, setInputMode] = useState('files') // 'files' | 'diff'
  const [fileInput, setFileInput] = useState('shoplite/db.py')
  const [diffInput, setDiffInput] = useState('')

  useEffect(() => {
    if (project?.id) {
      loadHistory()
    }
  }, [project?.id])

  async function loadHistory() {
    try {
      setLoading(true)
      const list = await api.listChangeAnalyses(project.id)
      setHistory(list || [])
      if (list && list.length > 0) {
        setAnalysis(list[0])
      }
    } catch (err) {
      setError(err)
    } finally {
      setLoading(false)
    }
  }

  async function handleAnalyze() {
    if (!project?.id) return
    setError(null)
    setLoading(true)
    try {
      const payload = {
        title: inputMode === 'diff' ? 'Custom Git Diff Analysis' : `Change in ${fileInput.split(',').length} file(s)`,
        run_validation: false,
        request_ai: true,
      }
      if (inputMode === 'diff') {
        payload.diff_text = diffInput
      } else {
        payload.files = fileInput.split(',').map((f) => f.trim()).filter(Boolean)
      }

      const res = await api.analyzeChange(project.id, payload)
      setAnalysis(res)
      setHistory((prev) => [res, ...prev.filter((h) => h.id !== res.id)])
    } catch (err) {
      setError(err)
    } finally {
      setLoading(false)
    }
  }

  async function handleRunDemo() {
    if (!project?.id) return
    setError(null)
    setLoading(true)
    try {
      const res = await api.runChangeDemo(project.id)
      setAnalysis(res)
      setHistory((prev) => [res, ...prev.filter((h) => h.id !== res.id)])
      setActiveTab('overview')
    } catch (err) {
      setError(err)
    } finally {
      setLoading(false)
    }
  }

  async function handleTriggerValidation() {
    if (!project?.id || !analysis?.id) return
    setValidating(true)
    setError(null)
    try {
      const updated = await api.validateChangeAnalysis(project.id, analysis.id)
      setAnalysis(updated)
      setHistory((prev) => prev.map((h) => (h.id === updated.id ? updated : h)))
      setActiveTab('validation')
    } catch (err) {
      setError(err)
    } finally {
      setValidating(false)
    }
  }

  if (projectLoading) {
    return <div className="p-8"><Spinner label="Loading project context..." /></div>
  }

  if (!project) {
    return (
      <EmptyState
        icon={GitPullRequest}
        title="No Project Selected"
        message="Please select or import a repository to run DevDNA Change Intelligence."
      />
    )
  }

  const rec = analysis?.recommendation || {}
  const risk = analysis?.risk_assessment || {}
  const diff = analysis?.graph_diff || {}
  const tests = analysis?.prioritized_tests?.tests || []
  const val = analysis?.validation
  const ai = analysis?.ai_explanation

  const decision = rec.decision || 'PENDING'
  const isBlock = decision === 'BLOCK RECOMMENDED'
  const isApprove = decision === 'APPROVE'
  const isReview = decision === 'REVIEW REQUIRED'

  const decisionBadgeColor = isApprove
    ? 'bg-positive/15 text-positive border-positive/30'
    : isBlock
    ? 'bg-negative/15 text-negative border-negative/30'
    : 'bg-warning/15 text-warning border-warning/30'

  return (
    <div className="space-y-6 pb-12">
      {/* Header */}
      <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <div className="flex items-center gap-2">
            <span className="flex h-6 w-6 items-center justify-center rounded-md bg-accent/20 text-accent">
              <GitPullRequest className="h-4 w-4" />
            </span>
            <span className="text-xs font-semibold tracking-wider text-accent uppercase">
              Software Reliability Intelligence
            </span>
          </div>
          <h1 className="mt-1 text-2xl font-bold tracking-tight text-ink">DevDNA Change Intelligence</h1>
          <p className="mt-0.5 text-sm text-muted">
            Know what your code change could break before you merge it.
          </p>
        </div>

        {/* Sample Scenario & Actions */}
        <div className="flex flex-wrap items-center gap-2.5">
          <ExplainAnalysisButton kind="change_intelligence" data={analysis} />
          {history.length > 1 && (
            <select
              aria-label="Select previous change analysis"
              value={analysis?.id || ''}
              onChange={(e) => {
                const chosen = history.find((h) => h.id === e.target.value)
                if (chosen) setAnalysis(chosen)
              }}
              className="h-9 max-w-[220px] truncate rounded-control border border-edge bg-surface px-2.5 text-xs text-ink outline-none focus:border-accent"
            >
              {history.map((h) => (
                <option key={h.id} value={h.id}>
                  {h.title || h.id} ({h.risk_assessment?.score ?? 0}/100)
                </option>
              ))}
            </select>
          )}
          <Button
            variant="outline"
            onClick={handleRunDemo}
            disabled={loading}
            icon={Play}
            className="border-accent/40 bg-accent/10 text-accent hover:bg-accent/20"
          >
            {loading ? 'Analyzing...' : 'Run Sample Scenario: DB Handler'}
          </Button>
          {analysis && !val?.executed && (
            <Button
              variant="default"
              onClick={handleTriggerValidation}
              disabled={validating}
              icon={Zap}
            >
              {validating ? 'Validating...' : 'Run Controlled Validation'}
            </Button>
          )}
        </div>
      </div>

      {error && (
        <ErrorState
          error={error}
          onRetry={loadHistory}
          className="my-2"
        />
      )}

      {/* Input / Execution Bar */}
      <Card className="p-4 bg-surface/70 border-edge">
        <div className="flex flex-col gap-4 md:flex-row md:items-center md:justify-between">
          <div className="flex items-center gap-2">
            <button
              type="button"
              onClick={() => setInputMode('files')}
              className={`px-3 py-1.5 text-xs font-medium rounded-control transition-colors ${
                inputMode === 'files'
                  ? 'bg-accent/20 text-accent border border-accent/30'
                  : 'text-muted hover:text-ink'
              }`}
            >
              Changed Files List
            </button>
            <button
              type="button"
              onClick={() => setInputMode('diff')}
              className={`px-3 py-1.5 text-xs font-medium rounded-control transition-colors ${
                inputMode === 'diff'
                  ? 'bg-accent/20 text-accent border border-accent/30'
                  : 'text-muted hover:text-ink'
              }`}
            >
              Unified Git Diff
            </button>
          </div>

          <div className="flex flex-1 items-center gap-2 max-w-xl">
            {inputMode === 'files' ? (
              <input
                type="text"
                value={fileInput}
                onChange={(e) => setFileInput(e.target.value)}
                placeholder="Comma-separated files: shoplite/db.py, shoplite/pricing.py"
                className="flex-1 rounded-control border border-edge bg-base px-3 py-1.5 text-xs text-ink placeholder:text-faint focus:border-accent focus:outline-none"
              />
            ) : (
              <textarea
                value={diffInput}
                onChange={(e) => setDiffInput(e.target.value)}
                placeholder="Paste unified git diff..."
                rows={1}
                className="flex-1 rounded-control border border-edge bg-base px-3 py-1.5 text-xs text-ink placeholder:text-faint focus:border-accent focus:outline-none font-mono"
              />
            )}
            <Button
              size="sm"
              onClick={handleAnalyze}
              disabled={loading}
              icon={GitPullRequest}
            >
              {loading ? 'Analyzing...' : 'Analyze Change'}
            </Button>
          </div>
        </div>
      </Card>

      {/* Main Analysis Display */}
      {loading && !analysis ? (
        <Card className="p-12 text-center">
          <Spinner label="Executing AST parsing, graph propagation, and change risk calculation..." />
        </Card>
      ) : !analysis ? (
        <EmptyState
          icon={GitPullRequest}
          title="No Change Analysis Run Yet"
          message="Click 'Run Sample Scenario: DB Handler' to run the bundled end-to-end database handler scenario, or input changed files above."
          action={
            <Button onClick={handleRunDemo} icon={Play}>
              Run Sample Scenario
            </Button>
          }
        />
      ) : (
        <div className="space-y-6">
          {/* Executive Recommendation Banner */}
          <Card className={`p-5 border ${decisionBadgeColor.split(' ')[2] || 'border-edge'} bg-surface/90 shadow-lg relative overflow-hidden`}>
            <div className="flex flex-col md:flex-row md:items-center justify-between gap-4">
              <div className="flex items-start gap-4">
                <div className="mt-0.5">
                  {isApprove && <CheckCircle2 className="h-8 w-8 text-positive shrink-0" />}
                  {isBlock && <XCircle className="h-8 w-8 text-negative shrink-0" />}
                  {isReview && <AlertTriangle className="h-8 w-8 text-warning shrink-0" />}
                </div>
                <div>
                  <div className="flex items-center gap-2.5">
                    <span className={`px-2.5 py-0.5 rounded-full text-xs font-bold uppercase tracking-wide border ${decisionBadgeColor}`}>
                      {decision}
                    </span>
                    <span className="text-xs text-muted">
                      Analyzed at {new Date(analysis.created_at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}
                    </span>
                    {val?.executed && (
                      <span className="flex items-center gap-1 text-xs text-accent font-medium">
                        <Zap className="h-3 w-3" /> Empirically Verified
                      </span>
                    )}
                  </div>
                  <h2 className="mt-1 text-lg font-semibold text-ink">
                    {rec.headline || 'Change analysis completed.'}
                  </h2>
                  <p className="mt-0.5 text-xs text-muted max-w-3xl">
                    {analysis.title || 'Code Change Analysis'} — {analysis.diff_manifest?.summary?.total_files || 0} file(s) changed, {diff.summary?.directly_affected_count || 0} direct component(s) modified.
                  </p>
                </div>
              </div>

              {/* Overall Risk Score Badge */}
              <div className="flex items-center gap-6 border-t md:border-t-0 md:border-l border-edge pt-3 md:pt-0 md:pl-6">
                <div className="text-right">
                  <div className="flex items-center justify-end gap-1 text-xs font-medium text-muted uppercase tracking-wider">
                    <TermExplainer termId="risk_scoring">Change Risk Score</TermExplainer>
                  </div>
                  <div className="flex items-baseline justify-end gap-1.5 mt-0.5">
                    <span className={`text-3xl font-extrabold ${
                      risk.level === 'high' ? 'text-negative' : risk.level === 'medium' ? 'text-warning' : 'text-positive'
                    }`}>
                      {risk.score ?? 0}
                    </span>
                    <span className="text-xs text-faint">/ 100</span>
                  </div>
                  <div className="text-xs capitalize font-medium text-muted">
                    {risk.level || 'low'} Risk Impact
                  </div>
                </div>
              </div>
            </div>

            {/* Blockers or Warnings if any */}
            {rec.blockers && rec.blockers.length > 0 && (
              <div className="mt-4 pt-3 border-t border-edge flex items-start gap-2 text-xs text-negative">
                <ShieldAlert className="h-4 w-4 shrink-0 mt-0.5" />
                <div>
                  <span className="font-semibold">Blockers: </span>
                  {rec.blockers.join(' ')}
                </div>
              </div>
            )}
          </Card>

          {/* Quick Metrics Strip */}
          <StatStrip>
            <Stat
              label="Direct Components"
              value={diff.summary?.directly_affected_count || 0}
              sub={`${analysis.diff_manifest?.summary?.changed_functions || 0} functions modified`}
              icon={FileCode2}
            />
            <Stat
              label="Transitive Dependents"
              value={diff.summary?.transitively_affected_count || 0}
              sub={`${diff.critical_components_affected?.length || 0} critical components`}
              icon={Network}
            />
            <Stat
              label="Impacted API Routes"
              value={diff.summary?.impacted_routes_count || 0}
              sub={diff.impacted_routes?.length > 0 ? diff.impacted_routes[0].route : 'No routes exposed'}
              icon={Layers}
            />
            <Stat
              label="Prioritized Tests"
              value={analysis.prioritized_tests?.summary?.total_prioritized_tests || 0}
              sub={`Tier 1: ${analysis.prioritized_tests?.summary?.tier_1_direct || 0} direct`}
              icon={TestTube2}
            />
          </StatStrip>

          {/* Tab Navigation */}
          <div className="flex border-b border-edge">
            {TABS.map((tab) => {
              const Icon = tab.icon
              const isActive = activeTab === tab.id
              return (
                <button
                  key={tab.id}
                  onClick={() => setActiveTab(tab.id)}
                  className={`flex items-center gap-2 px-4 py-2.5 text-xs font-medium border-b-2 transition-colors ${
                    isActive
                      ? 'border-accent text-ink bg-accent/5'
                      : 'border-transparent text-muted hover:text-ink hover:border-edge'
                  }`}
                >
                  <Icon className={`h-4 w-4 ${isActive ? 'text-accent' : 'text-muted'}`} />
                  {tab.label}
                  {tab.id === 'validation' && val?.executed && (
                    <span className="h-1.5 w-1.5 rounded-full bg-accent" />
                  )}
                </button>
              )
            })}
          </div>

          {/* Tab Content Views */}
          <div className="pt-2">
            {activeTab === 'overview' && (
              <ChangeOverviewTab analysis={analysis} onTriggerValidation={handleTriggerValidation} validating={validating} />
            )}
            {activeTab === 'impact' && (
              <ImpactGraphTab graphDiff={diff} />
            )}
            {activeTab === 'risk' && (
              <RiskBreakdownTab riskAssessment={risk} />
            )}
            {activeTab === 'tests' && (
              <TestIntelligenceTab prioritizedTests={analysis.prioritized_tests} validation={val} />
            )}
            {activeTab === 'validation' && (
              <ValidationReportTab
                validation={val}
                onTriggerValidation={handleTriggerValidation}
                validating={validating}
              />
            )}
            {activeTab === 'ai' && (
              <AIExplanationTab aiExplanation={ai} recommendation={rec} />
            )}
          </div>
        </div>
      )}
    </div>
  )
}

// ----------------------------------------------------------------------------- View 1: Overview Tab
function ChangeOverviewTab({ analysis, onTriggerValidation, validating }) {
  const diffManifest = analysis.diff_manifest || {}
  const files = diffManifest.files || []
  const rec = analysis.recommendation || {}
  const val = analysis.validation

  return (
    <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
      {/* Changed Files & Diffs */}
      <div className="lg:col-span-2 space-y-4">
        <Card>
          <CardHeader
            title="Changed Files & Components"
            subtitle={`${files.length} file(s) modified in proposed change`}
          />
          <div className="divide-y divide-edge">
            {files.map((f) => (
              <div key={f.path} className="p-4 space-y-2">
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-2">
                    <FileCode2 className="h-4 w-4 text-muted" />
                    <span className="font-mono text-xs font-semibold text-ink">{f.path}</span>
                    <span className={`px-1.5 py-0.5 text-[10px] uppercase font-bold rounded ${
                      f.status === 'added' ? 'bg-positive/20 text-positive' :
                      f.status === 'deleted' ? 'bg-negative/20 text-negative' :
                      f.status === 'renamed' ? 'bg-warning/20 text-warning' : 'bg-accent/20 text-accent'
                    }`}>
                      {f.status}
                    </span>
                  </div>
                  <span className="text-[11px] text-faint">
                    {f.granularity === 'function_level' ? 'AST Function Precision' : 'File-Level'}
                  </span>
                </div>

                {/* Changed components */}
                {f.changed_components && f.changed_components.length > 0 && (
                  <div className="pl-6 space-y-1.5">
                    <div className="text-[11px] text-muted font-medium">Modified Functions & Classes:</div>
                    <div className="flex flex-wrap gap-1.5">
                      {f.changed_components.map((comp) => (
                        <div
                          key={comp.id}
                          className="flex items-center gap-1.5 px-2 py-1 rounded bg-surface border border-edge text-xs font-mono text-ink"
                        >
                          <span className="h-1.5 w-1.5 rounded-full bg-accent" />
                          <span>{comp.name}</span>
                          <span className="text-[10px] text-faint">({comp.type})</span>
                        </div>
                      ))}
                    </div>
                  </div>
                )}

                {/* Changed imports */}
                {f.changed_imports && f.changed_imports.length > 0 && (
                  <div className="pl-6 text-[11px] text-muted">
                    <span className="text-faint">Imports changed: </span>
                    {f.changed_imports.map((i) => i.name).join(', ')}
                  </div>
                )}
              </div>
            ))}
          </div>
        </Card>

        {/* Evidence & Decision Details */}
        <Card>
          <CardHeader
            title="Evidence-Backed Recommendation"
            subtitle="Deterministic decision grounds based on static reachability and empirical findings"
          />
          <div className="p-4 space-y-3">
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
              {(rec.evidence || []).map((ev, i) => (
                <div key={i} className="p-3 rounded-card bg-surface border border-edge text-xs space-y-1">
                  <div className="font-semibold text-ink flex items-center gap-1.5">
                    <Info className="h-3.5 w-3.5 text-accent" />
                    {ev.title}
                  </div>
                  <div className="text-muted leading-relaxed">{ev.description}</div>
                </div>
              ))}
            </div>

            {rec.recommended_actions && rec.recommended_actions.length > 0 && (
              <div className="mt-4 pt-3 border-t border-edge">
                <div className="text-xs font-semibold text-ink mb-2">Recommended Next Steps for Developer:</div>
                <ul className="space-y-1.5">
                  {rec.recommended_actions.map((act, i) => (
                    <li key={i} className="flex items-start gap-2 text-xs text-muted">
                      <ChevronRight className="h-3.5 w-3.5 text-accent shrink-0 mt-0.5" />
                      <span>{act}</span>
                    </li>
                  ))}
                </ul>
              </div>
            )}
          </div>
        </Card>
      </div>

      {/* Sidebar: Validation Status & Limitations */}
      <div className="space-y-4">
        <Card>
          <CardHeader title="Controlled Validation" subtitle="Empirical proof of predicted impact" />
          <div className="p-4 space-y-3">
            {val?.executed ? (
              <div className="space-y-2">
                <div className="flex items-center gap-2 text-xs text-positive font-medium">
                  <CheckCircle2 className="h-4 w-4" /> Experiment Executed
                </div>
                <div className="text-xs text-muted">
                  Fault tested: <span className="font-mono text-ink">{val.fault?.kind}</span> on {val.fault?.target}.
                </div>
                <div className="p-2.5 rounded bg-surface border border-edge text-xs space-y-1">
                  <div className="flex justify-between">
                    <span className="text-muted">Baseline:</span>
                    <span className="text-positive font-mono">{val.baseline_summary?.passed} passed</span>
                  </div>
                  <div className="flex justify-between">
                    <span className="text-muted">With Fault:</span>
                    <span className="text-negative font-mono">{val.faulted_summary?.failed} failed</span>
                  </div>
                  <div className="flex justify-between pt-1 border-t border-edge">
                    <span className="text-muted">Accuracy:</span>
                    <span className="text-ink font-semibold">{val.accuracy ? `${Math.round(val.accuracy * 100)}%` : 'Calibrated'}</span>
                  </div>
                </div>
              </div>
            ) : (
              <div className="space-y-3">
                <p className="text-xs text-muted">
                  Empirical validation executes a controlled fault injection experiment in an isolated harness to verify if predicted failures actually occur.
                </p>
                <Button
                  size="sm"
                  variant="outline"
                  onClick={onTriggerValidation}
                  disabled={validating}
                  icon={Play}
                  className="w-full"
                >
                  {validating ? 'Running Experiment...' : 'Execute Validation Harness'}
                </Button>
              </div>
            )}
          </div>
        </Card>

        <Card>
          <CardHeader title="Analysis Guardrails" subtitle="Honesty rules and limitations" />
          <div className="p-4 space-y-2 text-xs text-muted">
            <div className="flex items-start gap-2">
              <Shield className="h-4 w-4 text-accent shrink-0 mt-0.5" />
              <span>Static reachability is reported as potential impact, never a guaranteed runtime failure.</span>
            </div>
            <div className="flex items-start gap-2">
              <Info className="h-4 w-4 text-accent shrink-0 mt-0.5" />
              <span>Predictions are validated only when controlled experiments execute successfully.</span>
            </div>
          </div>
        </Card>
      </div>
    </div>
  )
}

// ----------------------------------------------------------------------------- View 2: Impact Graph Tab
function ImpactGraphTab({ graphDiff }) {
  const directly = graphDiff.directly_affected || []
  const transitively = graphDiff.transitively_affected || []
  const potentially = graphDiff.potentially_affected || []
  const routes = graphDiff.impacted_routes || []
  const newDeps = graphDiff.new_dependencies || []

  return (
    <div className="space-y-6">
      {/* Visual Hierarchy Cards */}
      <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
        <Card className="border-accent/40 bg-accent/5 p-4">
          <div className="flex items-center gap-2 text-xs font-semibold text-accent uppercase tracking-wider">
            <span className="h-2 w-2 rounded-full bg-accent" />
            1. Directly Modified ({directly.length})
          </div>
          <div className="mt-2 text-xs text-muted">
            Roots of the change: functions and classes directly modified in the diff.
          </div>
          <div className="mt-3 space-y-1.5 max-h-48 overflow-y-auto">
            {directly.map((c) => (
              <div key={c.id} className="p-2 rounded bg-surface border border-edge text-xs font-mono text-ink">
                <div>{c.name}</div>
                <div className="text-[10px] text-faint">{c.file}:{c.line || 1}</div>
              </div>
            ))}
          </div>
        </Card>

        <Card className="border-warning/40 bg-warning/5 p-4">
          <div className="flex items-center gap-2 text-xs font-semibold text-warning uppercase tracking-wider">
            <span className="h-2 w-2 rounded-full bg-warning" />
            2. Transitive Dependents ({transitively.length})
          </div>
          <div className="mt-2 text-xs text-muted">
            Downstream services that call or import modified components.
          </div>
          <div className="mt-3 space-y-1.5 max-h-48 overflow-y-auto">
            {transitively.map((c) => (
              <div key={c.id} className="p-2 rounded bg-surface border border-edge text-xs font-mono text-ink">
                <div className="flex justify-between items-center">
                  <span>{c.name}</span>
                  <span className="text-[10px] text-muted">hop {c.distance}</span>
                </div>
                <div className="text-[10px] text-faint">{c.file}</div>
              </div>
            ))}
            {transitively.length === 0 && <div className="text-xs text-faint italic">No transitive dependents.</div>}
          </div>
        </Card>

        <Card className="border-negative/40 bg-negative/5 p-4">
          <div className="flex items-center gap-2 text-xs font-semibold text-negative uppercase tracking-wider">
            <span className="h-2 w-2 rounded-full bg-negative" />
            3. Exposed API Routes ({routes.length})
          </div>
          <div className="mt-2 text-xs text-muted">
            Client-facing HTTP endpoints reachable in the propagation path.
          </div>
          <div className="mt-3 space-y-1.5 max-h-48 overflow-y-auto">
            {routes.map((r) => (
              <div key={r.id} className="p-2 rounded bg-surface border border-edge text-xs font-mono text-ink">
                <div className="font-semibold text-negative">{r.route}</div>
                <div className="text-[10px] text-faint">{r.file} (distance {r.distance})</div>
              </div>
            ))}
            {routes.length === 0 && <div className="text-xs text-faint italic">No API routes exposed.</div>}
          </div>
        </Card>
      </div>

      {/* Newly Introduced Dependencies */}
      {newDeps.length > 0 && (
        <Card className="p-4">
          <CardHeader
            title="Newly Introduced Dependencies"
            subtitle="New architectural edges detected in modified revision that did not exist in base branch"
          />
          <div className="mt-3 divide-y divide-edge">
            {newDeps.map((dep, idx) => (
              <div key={idx} className="py-2.5 flex items-center justify-between text-xs font-mono">
                <div className="flex items-center gap-2">
                  <span className="text-ink">{dep.source_name}</span>
                  <ArrowRight className="h-3.5 w-3.5 text-accent" />
                  <span className="text-accent font-semibold">{dep.target_name}</span>
                </div>
                <span className="px-2 py-0.5 rounded bg-surface border border-edge text-[10px] text-muted uppercase">
                  {dep.type}
                </span>
              </div>
            ))}
          </div>
        </Card>
      )}
    </div>
  )
}

// ----------------------------------------------------------------------------- View 3: Risk Breakdown Tab
function RiskBreakdownTab({ riskAssessment }) {
  const factors = riskAssessment.factors || []
  const uncertainty = riskAssessment.uncertainty || {}

  return (
    <div className="space-y-6">
      <Card className="p-6">
        <div className="flex flex-col md:flex-row md:items-center justify-between gap-4 pb-6 border-b border-edge">
          <div>
            <h3 className="text-base font-semibold text-ink">Transparent Risk Calculation</h3>
            <p className="text-xs text-muted mt-0.5">
              Every score is explainable with explicit factor weights, empirical track records, and uncertainty penalties.
            </p>
          </div>
          <div className="flex items-center gap-3">
            <span className="text-xs text-muted uppercase tracking-wider font-medium">Composite Score:</span>
            <span className="text-2xl font-extrabold text-ink">{riskAssessment.score}/100</span>
          </div>
        </div>

        {/* Factors Breakdown Table */}
        <div className="mt-6 space-y-4">
          {factors.map((f) => (
            <div key={f.name} className="p-4 rounded-card bg-surface/50 border border-edge space-y-2">
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-2">
                  <span className="text-xs font-semibold text-ink">{f.name}</span>
                  <span className="text-[10px] text-faint font-mono">Weight: {Math.round(f.weight * 100)}%</span>
                </div>
                <div className="flex items-center gap-3 font-mono text-xs">
                  <span className="text-muted">Raw: {f.raw_score}/100</span>
                  <span className="text-accent font-bold">Contribution: +{f.weighted_score}</span>
                </div>
              </div>

              {/* Progress bar */}
              <div className="h-1.5 w-full bg-base rounded-full overflow-hidden">
                <div
                  className={`h-full rounded-full ${
                    f.raw_score >= 70 ? 'bg-negative' : f.raw_score >= 40 ? 'bg-warning' : 'bg-positive'
                  }`}
                  style={{ width: `${f.raw_score}%` }}
                />
              </div>

              <div className="text-xs text-muted leading-relaxed">
                {f.reason}
              </div>
            </div>
          ))}
        </div>
      </Card>

      {/* Uncertainty Quantification */}
      <Card className="p-5 border-edge">
        <div className="flex items-center gap-2 text-xs font-semibold text-ink uppercase tracking-wider">
          <Info className="h-4 w-4 text-accent" />
          Uncertainty Quantification (Score: {uncertainty.score ?? 0}/100)
        </div>
        <div className="mt-2 text-xs text-muted space-y-1">
          {(uncertainty.limitations || []).map((lim, i) => (
            <div key={i} className="flex items-center gap-2">
              <span className="h-1 w-1 rounded-full bg-faint" />
              <span>{lim}</span>
            </div>
          ))}
        </div>
      </Card>
    </div>
  )
}

// ----------------------------------------------------------------------------- View 4: Test Intelligence Tab
function TestIntelligenceTab({ prioritizedTests, validation }) {
  const tests = prioritizedTests?.tests || []
  const summary = prioritizedTests?.summary || {}
  const [selectedTier, setSelectedTier] = useState('all')

  const filteredTests = selectedTier === 'all'
    ? tests
    : tests.filter((t) => String(t.tier) === selectedTier)

  return (
    <div className="space-y-6">
      {/* 5-Tiered Summary Pills */}
      <div className="flex flex-wrap gap-2">
        <button
          onClick={() => setSelectedTier('all')}
          className={`px-3 py-1.5 rounded-control text-xs font-medium transition-colors ${
            selectedTier === 'all' ? 'bg-accent text-white' : 'bg-surface text-muted border border-edge hover:text-ink'
          }`}
        >
          All Prioritized ({tests.length})
        </button>
        {[1, 2, 3, 4, 5].map((tier) => (
          <button
            key={tier}
            onClick={() => setSelectedTier(String(tier))}
            className={`px-3 py-1.5 rounded-control text-xs font-medium transition-colors ${
              selectedTier === String(tier)
                ? 'bg-accent text-white'
                : 'bg-surface text-muted border border-edge hover:text-ink'
            }`}
          >
            Tier {tier} ({tests.filter((t) => t.tier === tier).length})
          </button>
        ))}
      </div>

      {/* Tests List */}
      <Card>
        <CardHeader
          title="Prioritized Test Suite"
          subtitle="Ranked by structural proximity to modified components and fault exposure"
        />
        <div className="divide-y divide-edge">
          {filteredTests.map((t, idx) => (
            <div key={idx} className="p-4 space-y-2 hover:bg-surface/40 transition-colors">
              <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-2">
                <div className="flex items-center gap-2">
                  <span className={`px-2 py-0.5 rounded text-[10px] font-bold uppercase font-mono ${
                    t.tier === 1 ? 'bg-positive/20 text-positive border border-positive/30' :
                    t.tier === 2 ? 'bg-accent/20 text-accent border border-accent/30' :
                    t.tier === 4 ? 'bg-warning/20 text-warning border border-warning/30' : 'bg-surface text-muted border border-edge'
                  }`}>
                    Tier {t.tier}: {t.tier_name}
                  </span>
                  <span className="font-mono text-xs font-semibold text-ink">{t.name}</span>
                </div>
                <div className="flex items-center gap-3 text-xs">
                  <span className="text-faint font-mono">Priority: {t.priority_score}</span>
                  <span className={`px-2 py-0.5 rounded text-[10px] uppercase font-semibold ${
                    t.coverage_confidence === 'high' ? 'bg-positive/10 text-positive' : 'bg-warning/10 text-warning'
                  }`}>
                    {t.coverage_confidence} confidence
                  </span>
                </div>
              </div>

              <div className="text-xs text-muted flex items-start gap-1.5">
                <ChevronRight className="h-3.5 w-3.5 text-accent shrink-0 mt-0.5" />
                <span>{t.selection_reason}</span>
              </div>

              {t.nodeid && (
                <div className="text-[11px] font-mono text-faint pl-5">
                  <code className="text-faint">pytest {t.nodeid}</code>
                </div>
              )}
            </div>
          ))}
          {filteredTests.length === 0 && (
            <div className="p-8 text-center text-xs text-faint">No tests match the selected tier.</div>
          )}
        </div>
      </Card>
    </div>
  )
}

// ----------------------------------------------------------------------------- View 5: Validation Report Tab
function ValidationReportTab({ validation, onTriggerValidation, validating }) {
  if (!validation || !validation.executed) {
    return (
      <Card className="p-8 text-center space-y-4">
        <div className="flex justify-center">
          <Zap className="h-10 w-10 text-accent/60" />
        </div>
        <div className="max-w-md mx-auto space-y-2">
          <h3 className="text-base font-semibold text-ink">Controlled Validation Not Run</h3>
          <p className="text-xs text-muted leading-relaxed">
            Execute a controlled fault injection experiment on the disposable project harness to observe whether predicted failures happen in practice.
          </p>
          <Button
            onClick={onTriggerValidation}
            disabled={validating}
            icon={Play}
            className="mt-3"
          >
            {validating ? 'Running Experiment Harness...' : 'Execute Validation Harness'}
          </Button>
        </div>
      </Card>
    )
  }

  const base = validation.baseline_summary || {}
  const fault = validation.faulted_summary || {}
  const metrics = validation.metrics || {}

  return (
    <div className="space-y-6">
      {/* Experiment Overview Cards */}
      <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
        <Card className="p-4 border-positive/30 bg-positive/5">
          <div className="text-xs font-semibold text-positive uppercase tracking-wider">Baseline Run</div>
          <div className="mt-2 text-2xl font-bold text-ink">{base.passed || 0} passed</div>
          <div className="text-xs text-muted mt-1">Duration: {base.duration ? `${base.duration.toFixed(2)}s` : 'Fast'}</div>
        </Card>

        <Card className="p-4 border-negative/30 bg-negative/5">
          <div className="text-xs font-semibold text-negative uppercase tracking-wider">Faulted Run ({validation.fault?.kind})</div>
          <div className="mt-2 text-2xl font-bold text-ink">{fault.failed || 0} failed, {fault.passed || 0} passed</div>
          <div className="text-xs text-muted mt-1">Fault triggered: {fault.fault_triggered ? 'Yes' : 'No'}</div>
        </Card>

        <Card className="p-4 border-accent/30 bg-accent/5">
          <div className="text-xs font-semibold text-accent uppercase tracking-wider">Prediction Accuracy</div>
          <div className="mt-2 text-2xl font-bold text-ink">
            {metrics.accuracy ? `${Math.round(metrics.accuracy * 100)}%` : 'Calibrated'}
          </div>
          <div className="text-xs text-muted mt-1">
            TP: {metrics.true_positive || 0} | FP: {metrics.false_positive || 0}
          </div>
        </Card>
      </div>

      {/* Confusion Matrix & Results Breakdown */}
      <Card>
        <CardHeader
          title="Prediction vs Reality Breakdown"
          subtitle="Direct comparison between predicted component impact and actual observed test outcomes"
        />
        <div className="p-4 space-y-4">
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 text-center">
            <div className="p-3 rounded bg-surface border border-edge">
              <div className="text-xs text-muted">Correct Predictions</div>
              <div className="text-xl font-bold text-positive mt-1">{validation.correct_predictions || 0}</div>
            </div>
            <div className="p-3 rounded bg-surface border border-edge">
              <div className="text-xs text-muted">False Positives</div>
              <div className="text-xl font-bold text-warning mt-1">{validation.false_positives || 0}</div>
            </div>
            <div className="p-3 rounded bg-surface border border-edge">
              <div className="text-xs text-muted">False Negatives</div>
              <div className="text-xl font-bold text-negative mt-1">{validation.false_negatives || 0}</div>
            </div>
            <div className="p-3 rounded bg-surface border border-edge">
              <div className="text-xs text-muted">Execution Mode</div>
              <div className="text-xs font-mono font-semibold text-ink mt-2 uppercase">{validation.mode}</div>
            </div>
          </div>
        </div>
      </Card>
    </div>
  )
}

// ----------------------------------------------------------------------------- View 6: AI Explanation Tab
function AIExplanationTab({ aiExplanation, recommendation }) {
  if (!aiExplanation) {
    return (
      <Card className="p-8 text-center text-xs text-faint">
        No AI explanation generated for this run.
      </Card>
    )
  }

  return (
    <div className="space-y-6">
      <Card className="p-6 space-y-6">
        <div>
          <div className="flex items-center gap-2 text-xs font-semibold text-accent uppercase tracking-wider">
            <Sparkles className="h-4 w-4" />
            Executive Summary
          </div>
          <p className="mt-2 text-sm text-ink leading-relaxed font-medium">
            {aiExplanation.summary}
          </p>
        </div>

        <div className="border-t border-edge pt-4">
          <div className="text-xs font-semibold text-muted uppercase tracking-wider">
            Failure Propagation Flow
          </div>
          <p className="mt-2 text-xs text-ink leading-relaxed">
            {aiExplanation.propagation_narrative}
          </p>
        </div>

        {aiExplanation.failure_risks && aiExplanation.failure_risks.length > 0 && (
          <div className="border-t border-edge pt-4">
            <div className="text-xs font-semibold text-muted uppercase tracking-wider mb-2">
              Specific Failure Risks Identified
            </div>
            <ul className="space-y-1.5">
              {aiExplanation.failure_risks.map((risk, i) => (
                <li key={i} className="flex items-start gap-2 text-xs text-muted">
                  <AlertTriangle className="h-3.5 w-3.5 text-warning shrink-0 mt-0.5" />
                  <span>{typeof risk === 'object' ? (risk.risk || risk.description || JSON.stringify(risk)) : risk}</span>
                </li>
              ))}
            </ul>
          </div>
        )}

        {aiExplanation.suggested_repairs && aiExplanation.suggested_repairs.length > 0 && (
          <div className="border-t border-edge pt-4">
            <div className="text-xs font-semibold text-muted uppercase tracking-wider mb-2">
              Suggested Architectural Repairs & Safeguards
            </div>
            <ul className="space-y-1.5">
              {aiExplanation.suggested_repairs.map((repair, i) => (
                <li key={i} className="flex items-start gap-2 text-xs text-muted">
                  <Check className="h-3.5 w-3.5 text-positive shrink-0 mt-0.5" />
                  <span>{typeof repair === 'object' ? (repair.repair || repair.description || JSON.stringify(repair)) : repair}</span>
                </li>
              ))}
            </ul>
          </div>
        )}

        <div className="border-t border-edge pt-4 flex items-center justify-between text-[11px] text-faint">
          <span>Source: {aiExplanation.source === 'gemini' ? 'Gemini Grounded Reasoning' : 'Deterministic Core Engine'}</span>
          <span>Cited Evidence: {aiExplanation.evidence_used?.join(', ') || 'Static Graph'}</span>
        </div>
      </Card>
    </div>
  )
}
