import { motion } from 'framer-motion'
import { CheckCircle2, Download, FileJson, FileText, History, Sparkles } from 'lucide-react'
import { useState } from 'react'
import { stagger } from '../animations/variants'
import ProjectGate from '../components/ProjectGate'
import { Badge, Button, Card, CardHeader, ErrorState, PageHeader, StatusBadge } from '../components/ui'
import { useAsync } from '../hooks/useAsync'
import { api, downloadFile } from '../services/api'
import { bytes, timeAgo } from '../utils/format'
import { shortId } from '../utils/graph'

export default function Reports() {
  return <ProjectGate>{(project) => <ReportsPage project={project} />}</ProjectGate>
}

function ReportsPage({ project }) {
  const history = useAsync(() => api.reportHistory(project.id), [project.id])
  const exps = useAsync(() => api.experiments(project.id), [project.id])
  const ai = useAsync(() => api.aiInteractions(project.id), [project.id])
  const [busy, setBusy] = useState(null)
  const [done, setDone] = useState(null)
  const [error, setError] = useState(null)

  const get = async (key, url, name) => {
    setBusy(key)
    setError(null)
    try {
      const file = await downloadFile(url, name)
      setDone(file)
      history.reload({ silent: true })
    } catch (e) {
      setError(e)
    } finally {
      setBusy(null)
    }
  }

  return (
    <>
      <PageHeader title="Reports and audit trail" subtitle="Downloadable Markdown and JSON built from persisted records. Credentials and secret-looking values are redacted." />
      {error && <div className="mb-4"><ErrorState error={error} /></div>}
      {done && (
        <motion.div initial={{ opacity: 0, y: -4 }} animate={{ opacity: 1, y: 0 }} className="mb-4 flex items-center gap-2 rounded-card border border-positive/25 bg-positive-bg px-3 py-2 text-sm text-ink">
          <CheckCircle2 className="h-4 w-4 text-positive" /> Downloaded {done}
        </motion.div>
      )}
      <motion.div variants={stagger} initial="initial" animate="animate" className="grid items-start gap-4 lg:grid-cols-2">
        <Card>
          <CardHeader title="Project report" subtitle="Analysis summary, critical components, findings, experiments, AI usage" icon={FileText} />
          <div className="flex flex-wrap gap-2 p-4">
            <Button variant="primary" icon={Download} loading={busy === 'p-md'} onClick={() => get('p-md', `/api/projects/${project.id}/report?format=md`, 'report.md')}>Markdown</Button>
            <Button icon={FileJson} loading={busy === 'p-json'} onClick={() => get('p-json', `/api/projects/${project.id}/report?format=json`, 'report.json')}>JSON</Button>
          </div>
        </Card>
        <Card>
          <CardHeader title="Experiment reports" icon={FileText} />
          <ul className="max-h-72 divide-y divide-rule overflow-y-auto">
            {(exps.data || []).map((e) => (
              <li key={e.id} className="flex flex-wrap items-center gap-2 px-4 py-2.5">
                <StatusBadge status={e.status} />
                <span className="min-w-0 flex-1 truncate text-sm text-muted">{e.prediction?.impact?.scenario_label}: {shortId(e.prediction?.target_id || '')}</span>
                <Button size="sm" icon={Download} loading={busy === `${e.id}-md`} onClick={() => get(`${e.id}-md`, `/api/experiments/${e.id}/report?format=md`, 'experiment.md')}>MD</Button>
                <Button size="sm" icon={FileJson} loading={busy === `${e.id}-json`} onClick={() => get(`${e.id}-json`, `/api/experiments/${e.id}/report?format=json`, 'experiment.json')}>JSON</Button>
              </li>
            ))}
            {exps.data?.length === 0 && <li className="p-4 text-sm text-faint">No experiments yet.</li>}
          </ul>
        </Card>
        <Card>
          <CardHeader title="Generated reports" icon={History} />
          <ul className="max-h-72 divide-y divide-rule overflow-y-auto text-sm">
            {(history.data || []).map((r) => (
              <li key={r.id} className="flex items-center gap-2 px-4 py-2">
                <Badge>{r.format}</Badge>
                <span className="flex-1 text-muted">{r.kind} report</span>
                <span className="num text-xs text-faint">{bytes(r.size_bytes)} · {timeAgo(r.created_at)}</span>
              </li>
            ))}
            {history.data?.length === 0 && <li className="p-4 text-faint">No reports generated yet.</li>}
          </ul>
        </Card>
        <Card>
          <CardHeader title="Gemma audit log" subtitle="Every AI request: what was sent and whether it succeeded" icon={Sparkles} />
          <ul className="max-h-72 divide-y divide-rule overflow-y-auto text-sm">
            {(ai.data || []).map((r) => (
              <li key={r.id} className="px-4 py-2">
                <div className="flex items-center gap-2">
                  <Badge className={r.status === 'ok' ? 'bg-positive-bg text-positive-ink' : 'bg-negative-bg text-negative-ink'}>{r.status}</Badge>
                  <span className="flex-1 text-muted">{r.kind.replace('_', ' ')}</span>
                  <span className="num text-xs text-faint">{r.model} · {timeAgo(r.created_at)}</span>
                </div>
                <div className="mt-0.5 text-xs text-faint">{r.evidence_refs.length} evidence items sent{r.error ? ` · ${r.error}` : ''}</div>
              </li>
            ))}
            {ai.data?.length === 0 && <li className="p-4 text-faint">No Gemma requests yet.</li>}
          </ul>
        </Card>
      </motion.div>
    </>
  )
}
