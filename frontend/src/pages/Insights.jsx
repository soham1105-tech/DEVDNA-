import { FlaskConical, GitPullRequest, ListChecks, ShieldQuestion, Sparkles } from 'lucide-react'
import { useState } from 'react'
import { Link } from 'react-router-dom'
import ProjectGate from '../components/ProjectGate'
import { Badge, Button, Card, CardHeader, EmptyState, ErrorState, InlineNotice, PageHeader, SeverityBadge, SkeletonCards, Stat, StatStrip } from '../components/ui'
import { useAsync } from '../hooks/useAsync'
import { api, toApiError } from '../services/api'

const TABS = [
  { id: 'pr', label: 'PR Review', icon: GitPullRequest },
  { id: 'gaps', label: 'Test Gaps', icon: ListChecks },
  { id: 'reliability', label: 'Reliability', icon: FlaskConical },
]

export default function Insights() {
  return <ProjectGate>{(project) => <InsightsPage project={project} />}</ProjectGate>
}

function InsightsPage({ project }) {
  const [tab, setTab] = useState('pr')
  return (
    <div>
      <PageHeader
        title="Insights"
        eyebrow="Deterministic modules"
        subtitle="Change-impact, test coverage and the experiment track record — static predictions and recorded observations, never verdicts."
      />
      <div className="mb-5 flex flex-wrap gap-2">
        {TABS.map(({ id, label, icon: Icon }) => (
          <button
            key={id}
            onClick={() => setTab(id)}
            className={`flex items-center gap-2 rounded-control border px-3 py-1.5 text-sm transition-colors ${
              tab === id ? 'border-accent bg-accent/10 text-accent' : 'border-edge bg-surface text-muted hover:border-control'
            }`}
          >
            <Icon className="h-4 w-4" />
            {label}
          </button>
        ))}
      </div>
      {tab === 'pr' && <PrReviewPanel project={project} />}
      {tab === 'gaps' && <TestGapsPanel project={project} />}
      {tab === 'reliability' && <ReliabilityPanel project={project} />}
    </div>
  )
}

/* ------------------------------- PR review ------------------------------- */

function PrReviewPanel({ project }) {
  const [filesText, setFilesText] = useState('')
  const [title, setTitle] = useState('')
  const [result, setResult] = useState(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)

  const run = async (e) => {
    e.preventDefault()
    const files = filesText.split('\n').map((f) => f.trim()).filter(Boolean)
    if (!files.length) return
    setLoading(true)
    setError(null)
    try {
      setResult(await api.prReview(project.id, { files, title: title.trim() || null }))
    } catch (err) {
      setError(err)
      setResult(null)
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="space-y-4">
      <Card>
        <CardHeader
          title="Review a proposed change"
          subtitle="Paste changed file paths (one per line; git-diff a/ b/ prefixes and backslashes are fine). Deterministic and read-only."
          icon={GitPullRequest}
        />
        <form onSubmit={run} className="space-y-3 p-4">
          <textarea
            value={filesText}
            onChange={(e) => setFilesText(e.target.value)}
            rows={4}
            placeholder={'backend/app/graph/engine.py\nfrontend/src/pages/Overview.jsx'}
            className="w-full rounded-control border border-edge bg-raised px-3 py-2 font-mono text-xs text-ink outline-none focus:border-accent"
          />
          <div className="flex flex-wrap items-center gap-2">
            <input
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              placeholder="PR title (optional)"
              className="min-w-0 flex-1 rounded-control border border-edge bg-raised px-3 py-2 text-sm text-ink outline-none focus:border-accent"
            />
            <Button type="submit" disabled={loading || !filesText.trim()}>
              {loading ? 'Computing…' : 'Review change'}
            </Button>
          </div>
        </form>
      </Card>

      {error && <ErrorState error={toApiError(error)} onRetry={run} />}
      {loading && <SkeletonCards />}
      {result && <PrReviewResult result={result} />}
    </div>
  )
}

function PrReviewResult({ result }) {
  const s = result.summary
  return (
    <div className="space-y-4">
      {result.title && <InlineNotice tone="neutral">{result.title}</InlineNotice>}
      <Card>
        <StatStrip>
          <Stat label="Files mapped" value={`${s.files_mapped}/${s.files}`} />
          <Stat label="Affected (possible)" value={s.affected_components} />
          <Stat label="Tests may break" value={s.tests_may_break} />
          <Stat label="Files w/o coverage" value={s.changed_files_without_test_coverage} />
          <Stat label="Touching findings" value={s.findings_touching_changed_files} />
        </StatStrip>
      </Card>

      <Card>
        <CardHeader title="Test coverage of the changed files" icon={ListChecks} />
        <div className="divide-y divide-rule">
          {result.test_coverage.map((c) => (
            <div key={c.file} className="flex items-center justify-between gap-3 px-4 py-2.5 text-sm">
              <span className="truncate font-mono text-xs text-ink">{c.file}</span>
              <CoverageBadge status={c.status} />
            </div>
          ))}
        </div>
      </Card>

      {result.findings.length > 0 && (
        <Card>
          <CardHeader title="Findings touching the changed files" icon={ShieldQuestion} />
          <div className="divide-y divide-rule">
            {result.findings.map((f) => (
              <div key={f.id} className="flex items-center gap-3 px-4 py-2.5 text-sm">
                <SeverityBadge severity={f.severity} />
                <span className="min-w-0 flex-1 truncate text-ink">{f.title}</span>
                <span className="hidden truncate font-mono text-xs text-faint sm:block">
                  {f.file}:{f.line}
                </span>
              </div>
            ))}
          </div>
        </Card>
      )}

      <Card>
        <CardHeader title="Next steps" icon={Sparkles} />
        <ol className="list-decimal space-y-1.5 px-8 py-4 text-sm text-muted">
          {result.next_steps.map((step, i) => (
            <li key={i}>{step}</li>
          ))}
        </ol>
      </Card>

      <LimitationsNote text={result.limitations} />
    </div>
  )
}

function CoverageBadge({ status }) {
  const map = {
    covered: 'bg-positive-bg text-positive-ink',
    partially_uncovered: 'bg-warning-bg text-warning-ink',
    uncovered: 'bg-negative-bg text-negative-ink',
  }
  const label = status === 'partially_uncovered' ? 'partially uncovered' : status
  return <Badge className={`shrink-0 ${map[status] || 'bg-neutral-bg text-neutral-ink'}`}>{label}</Badge>
}

/* ------------------------------- Test gaps ------------------------------- */

function TestGapsPanel({ project }) {
  const { data, error, loading, reload } = useAsync(() => api.testGaps(project.id), [project.id, project.latest_run_id])
  if (loading) return <SkeletonCards count={3} className="h-32" />
  if (error) return <ErrorState error={toApiError(error)} onRetry={reload} />
  if (!data) return null
  const s = data.summary

  return (
    <div className="space-y-4">
      <Card>
        <StatStrip>
          <Stat label="Code components" value={s.code_components} />
          <Stat label="Covered" value={s.covered_components} />
          <Stat label="Uncovered" value={s.uncovered_components} />
          <Stat label="Files with gaps" value={s.files_with_gaps} />
          <Stat label="Tests in graph" value={data.test_count} />
        </StatStrip>
      </Card>

      <InlineNotice tone="info">
        {data.validation.note}
      </InlineNotice>

      {data.file_gaps.length === 0 ? (
        <EmptyState
          icon={ListChecks}
          title="No static test gaps"
          description="Every analysed component is reachable from at least one test in the graph. This is a static claim, not proof of assertion quality."
        />
      ) : (
        <Card>
          <CardHeader title="Files with unreachable components" subtitle="Sorted by the number of components no test can reach." icon={ListChecks} />
          <div className="divide-y divide-rule">
            {data.file_gaps.slice(0, 50).map((g) => (
              <div key={g.file} className="flex items-center justify-between gap-3 px-4 py-2.5 text-sm">
                <span className="truncate font-mono text-xs text-ink">{g.file}</span>
                <Badge className="shrink-0 bg-warning-bg text-warning-ink">
                  {g.uncovered}/{g.components} unreachable
                </Badge>
              </div>
            ))}
          </div>
          {data.file_gaps.length > 50 && (
            <div className="px-4 py-2 text-xs text-faint">Showing 50 of {data.file_gaps.length} files.</div>
          )}
        </Card>
      )}
      <LimitationsNote text={data.limitations} />
    </div>
  )
}

/* ------------------------------- Reliability ------------------------------ */

function ReliabilityPanel({ project }) {
  const { data, error, loading, reload } = useAsync(() => api.reliability(project.id), [project.id])
  if (loading) return <SkeletonCards count={2} className="h-40" />
  if (error) return <ErrorState error={toApiError(error)} onRetry={reload} />
  if (!data) return null

  return (
    <div className="space-y-4">
      {data.status === 'empty' ? (
        <Card>
          <EmptyState
            icon={FlaskConical}
            title="No experiment observations recorded yet"
            description={data.note}
            action={<Link to="/lab"><Button>Open the What-If Lab</Button></Link>}
          />
        </Card>
      ) : (
        <>
          <Card>
            <StatStrip>
              <Stat label="Observations" value={data.summary.observations} />
              <Stat label="Experiments" value={data.summary.experiments} />
              <Stat label="Scenarios" value={data.summary.scenarios} />
              <Stat label="Claim patterns" value={data.summary.patterns} />
              <Stat label="Min sample for rates" value={data.summary.min_sample_for_rates} />
            </StatStrip>
          </Card>
          {data.by_scenario.map((sc) => (
            <Card key={sc.scenario}>
              <CardHeader title={sc.scenario} subtitle={`${sc.observations} observations from ${sc.experiments} experiment(s)`} icon={FlaskConical} />
              <div className="grid gap-x-6 gap-y-2 px-4 py-3 text-sm sm:grid-cols-2">
                <RateRow label="fail-prediction accuracy" rate={sc.fail_prediction_accuracy} />
                <RateRow label="pass-prediction accuracy" rate={sc.pass_prediction_accuracy} />
                <div className="text-muted">Uncertain observations: <span className="num text-ink">{sc.uncertain_observations}</span></div>
                <div className="text-muted">Excluded (no evidence): <span className="num text-ink">{sc.excluded_observations}</span></div>
              </div>
            </Card>
          ))}
        </>
      )}
      <LimitationsNote text={data.limitations} />
    </div>
  )
}

function RateRow({ label, rate }) {
  return (
    <div className="text-muted">
      {label}:{' '}
      {rate.status === 'measured' ? (
        <span className="num text-ink">{(rate.rate * 100).toFixed(1)}%</span>
      ) : (
        <Badge className="bg-neutral-bg text-neutral-ink">insufficient data ({rate.sample})</Badge>
      )}
    </div>
  )
}

function LimitationsNote({ text }) {
  return (
    <details className="rounded-card border border-edge bg-surface px-4 py-3 text-xs text-muted">
      <summary className="cursor-pointer text-faint">Method &amp; limitations</summary>
      <ul className="mt-2 list-disc space-y-1 pl-4">
        {(text || []).map((l, i) => (
          <li key={i}>{l}</li>
        ))}
      </ul>
    </details>
  )
}
