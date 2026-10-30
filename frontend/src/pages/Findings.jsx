import { AnimatePresence, motion } from 'framer-motion'
import { ChevronDown, Network, ShieldAlert } from 'lucide-react'
import { useMemo, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { EvidenceList } from '../components/graph/ComponentDrawer'
import ProjectGate from '../components/ProjectGate'
import { Badge, Button, Card, EmptyState, ErrorState, PageHeader, SeverityBadge, Skeleton } from '../components/ui'
import { useAsync } from '../hooks/useAsync'
import { api } from '../services/api'
import { shortId } from '../utils/graph'

const STATUSES = [
  ['open', 'Open'],
  ['acknowledged', 'Acknowledged'],
  ['resolved', 'Resolved'],
  ['false_positive', 'False positive'],
]

export default function Findings() {
  return <ProjectGate>{(project) => <FindingsPage project={project} />}</ProjectGate>
}

function FindingsPage({ project }) {
  const [params] = useSearchParams()
  const { data, error, loading, reload, setData } = useAsync(() => api.findings(project.id), [project.id, project.latest_run_id])
  const [severity, setSeverity] = useState('all')
  const [status, setStatus] = useState('all')
  const [owner, setOwner] = useState('all')
  const [query, setQuery] = useState('')
  const [open, setOpen] = useState(params.get('open'))
  const [saving, setSaving] = useState(null)
  const [actionError, setActionError] = useState('')

  const owners = useMemo(() => [...new Set((data || []).map((f) => f.owner || 'Unassigned'))].sort(), [data])
  const list = useMemo(
    () =>
      (data || []).filter(
        (f) =>
          (severity === 'all' || f.severity === severity) &&
          (status === 'all' || (status === 'suppressed' ? f.is_suppressed : status === 'active' ? !f.is_suppressed : f.review_status === status)) &&
          (owner === 'all' || (f.owner || 'Unassigned') === owner) &&
          (!query || `${f.title} ${f.description} ${f.component_id} ${f.rule}`.toLowerCase().includes(query.toLowerCase())),
      ),
    [data, severity, status, owner, query],
  )
  const counts = useMemo(() => {
    const c = { all: data?.length || 0 }
    ;(data || []).forEach((f) => (c[f.severity] = (c[f.severity] || 0) + 1))
    return c
  }, [data])

  const review = async (f, review_status) => {
    setSaving(f.id)
    try {
      const updated = await api.updateFinding(f.id, review_status)
      setData((d) => d.map((x) => (x.id === f.id ? updated : x)))
    } finally {
      setSaving(null)
    }
  }

  const saveSuppression = async (f, payload) => {
    setSaving(f.id)
    setActionError('')
    try {
      const updated = await api.suppressFinding(f.id, payload)
      setData((d) => d.map((x) => (x.id === f.id ? updated : x)))
    } catch (error) {
      setActionError(error.message)
    } finally {
      setSaving(null)
    }
  }

  const removeSuppression = async (f) => {
    setSaving(f.id)
    setActionError('')
    try {
      const updated = await api.unsuppressFinding(f.id)
      setData((d) => d.map((x) => (x.id === f.id ? updated : x)))
    } catch (error) {
      setActionError(error.message)
    } finally {
      setSaving(null)
    }
  }

  const renderFinding = (f) => (
    <motion.li key={f.id} layout initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, scale: 0.98 }}>
      <Card className="overflow-hidden">
        <button className="flex w-full items-start gap-3 px-4 py-3 text-left" onClick={() => setOpen(open === f.id ? null : f.id)} aria-expanded={open === f.id}>
          <SeverityBadge severity={f.severity} />
          <div className="min-w-0 flex-1">
            <div className="text-sm font-medium text-ink">{f.title}</div>
            <div className="mt-0.5 flex flex-wrap gap-x-3 text-xs text-faint">
              <span className="num">{f.rule}</span>
              {f.component_id && <span className="num">{shortId(f.component_id)}</span>}
              <span>owner {f.owner || 'Unassigned'}</span>
              <span>confidence {f.confidence}</span>
            </div>
          </div>
          {f.is_suppressed && <Badge className="bg-warning-bg text-warning-ink">Suppressed</Badge>}
          <Badge className={f.review_status === 'open' ? 'bg-accent/15 text-accent' : 'bg-neutral-bg text-neutral-ink'}>
            {STATUSES.find(([v]) => v === f.review_status)?.[1]}
          </Badge>
          <motion.span animate={{ rotate: open === f.id ? 180 : 0 }}><ChevronDown className="h-4 w-4 text-faint" /></motion.span>
        </button>
        <AnimatePresence initial={false}>
          {open === f.id && (
            <motion.div initial={{ height: 0, opacity: 0 }} animate={{ height: 'auto', opacity: 1 }} exit={{ height: 0, opacity: 0 }} className="overflow-hidden border-t border-rule">
              <div className="grid gap-4 p-4 lg:grid-cols-2">
                <div className="space-y-3">
                  <p className="text-sm text-muted">{f.description}</p>
                  <div>
                    <div className="mb-1 text-xs font-medium text-ink">Mitigation</div>
                    <p className="text-sm text-muted">{f.mitigation}</p>
                  </div>
                  {f.assumptions.length > 0 && (
                    <div>
                      <div className="mb-1 text-xs font-medium text-ink">Assumptions</div>
                      <ul className="space-y-0.5 text-xs text-muted">{f.assumptions.map((a) => <li key={a}>• {a}</li>)}</ul>
                    </div>
                  )}
                  <div className="flex flex-wrap items-center gap-2 pt-1">
                    <label className="text-xs text-faint" htmlFor={`rs-${f.id}`}>Review status</label>
                    <select id={`rs-${f.id}`} value={f.review_status} disabled={saving === f.id} onChange={(e) => review(f, e.target.value)} className="h-8 rounded-control border border-control bg-surface px-2 text-xs text-ink">
                      {STATUSES.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
                    </select>
                    {f.component_id && (
                      <Link to={`/architecture?focus=${encodeURIComponent(f.component_id)}`}>
                        <Button size="sm" icon={Network}>Show in graph</Button>
                      </Link>
                    )}
                  </div>
                  {f.is_suppressed ? (
                    <div className="rounded-card border border-warning/25 bg-warning-bg p-3 text-xs text-muted">
                      <p>Suppressed by {f.suppression.owner} until {new Date(f.suppression.expires_at).toLocaleDateString()}</p>
                      <p className="mt-1">{f.suppression.reason}</p>
                      <button disabled={saving === f.id} onClick={() => removeSuppression(f)} className="mt-2 text-warning hover:underline">Remove suppression</button>
                    </div>
                  ) : (
                    <form
                      className="grid gap-2 rounded-card border border-edge p-3 sm:grid-cols-2"
                      onSubmit={(event) => {
                        event.preventDefault()
                        const fields = new FormData(event.currentTarget)
                        const expiry = fields.get('expiry')
                        saveSuppression(f, {
                          owner: fields.get('suppression_owner'),
                          reason: fields.get('reason'),
                          expires_at: new Date(`${expiry}T23:59:59Z`).toISOString(),
                        })
                      }}
                    >
                      <input name="suppression_owner" aria-label="Suppression owner" required maxLength={500} defaultValue={f.owner === 'Unassigned' ? '' : f.owner} placeholder="Owner" className="h-8 rounded-control border border-control bg-surface px-2 text-xs text-ink" />
                      <input name="expiry" aria-label="Suppression expiry" type="date" required min={new Date().toISOString().slice(0, 10)} defaultValue={new Date(Date.now() + 30 * 86400000).toISOString().slice(0, 10)} className="num h-8 rounded-control border border-control bg-surface px-2 text-xs text-ink" />
                      <input name="reason" aria-label="Suppression reason" required maxLength={2000} placeholder="Reason for suppression" className="h-8 rounded-control border border-control bg-surface px-2 text-xs text-ink sm:col-span-2" />
                      <button type="submit" disabled={saving === f.id} className="h-8 rounded-control border border-warning/40 bg-warning-bg px-3 text-xs text-warning-ink hover:border-warning sm:col-span-2">
                        Suppress until expiry
                      </button>
                    </form>
                  )}
                </div>
                <div>
                  <div className="mb-1 text-xs font-medium text-ink">Evidence</div>
                  <EvidenceList evidence={f.evidence} />
                </div>
              </div>
            </motion.div>
          )}
        </AnimatePresence>
      </Card>
    </motion.li>
  )

  const activeFindings = list.filter((f) => !f.is_suppressed)
  const suppressedFindings = list.filter((f) => f.is_suppressed)

  return (
    <>
      <PageHeader title="Reliability findings" subtitle="Deterministic rules applied to the analysed graph. Each finding lists its evidence, confidence, assumptions and a mitigation. Severity rules are documented in the README." />
      <div className="mb-4 flex flex-wrap items-center gap-2">
        {['all', 'high', 'medium', 'low', 'info'].map((s) => (
          <button key={s} onClick={() => setSeverity(s)} aria-pressed={severity === s} className={`relative rounded-control px-3 py-1.5 text-xs font-medium ${severity === s ? 'bg-raised text-ink' : 'text-faint hover:text-muted'}`}>
            <span className="relative capitalize">{s} <span className="num text-faint">{counts[s] || 0}</span></span>
          </button>
        ))}
        <select value={status} onChange={(e) => setStatus(e.target.value)} aria-label="Finding status filter" className="h-8 rounded-control border border-control bg-surface px-2 text-xs text-ink">
          <option value="all">All statuses</option>
          <option value="active">Active findings</option>
          <option value="suppressed">Suppressed findings</option>
          {STATUSES.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
        </select>
        <select value={owner} onChange={(e) => setOwner(e.target.value)} aria-label="Finding owner filter" className="h-8 rounded-control border border-control bg-surface px-2 text-xs text-ink">
          <option value="all">All owners</option>
          {owners.map((value) => <option key={value} value={value}>{value}</option>)}
        </select>
        <input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Search findings…" aria-label="Search findings" className="h-8 min-w-[180px] flex-1 rounded-control border border-control bg-surface px-3 text-xs text-ink outline-none focus:border-accent sm:max-w-xs" />
      </div>
      {loading && !data && <div className="space-y-2">{[0, 1, 2].map((i) => <Skeleton key={i} className="h-16" />)}</div>}
      {error && <ErrorState error={error} onRetry={reload} />}
      {actionError && <p role="alert" className="mb-3 text-sm text-negative">{actionError}</p>}
      {data && list.length === 0 && <EmptyState icon={ShieldAlert} title="No findings match" description="Try a different filter." />}
      {activeFindings.length > 0 && <section className="space-y-2"><h2 className="text-xs font-medium text-ink">Active findings</h2><motion.ul layout className="space-y-2"><AnimatePresence initial={false}>{activeFindings.map(renderFinding)}</AnimatePresence></motion.ul></section>}
      {suppressedFindings.length > 0 && <section className="mt-6 space-y-2"><h2 className="text-xs font-medium text-warning">Suppressed findings</h2><motion.ul layout className="space-y-2"><AnimatePresence initial={false}>{suppressedFindings.map(renderFinding)}</AnimatePresence></motion.ul></section>}
    </>
  )
}
