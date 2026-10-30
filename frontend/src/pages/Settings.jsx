import { motion } from 'framer-motion'
import { Cpu, Gauge, KeyRound, Moon, Server, ShieldCheck, Sparkles, Sun, Trash2 } from 'lucide-react'
import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { stagger } from '../animations/variants'
import { Badge, Button, Card, CardHeader, ErrorState, Modal, PageHeader } from '../components/ui'
import { writeStore } from '../hooks/storage'
import { useProject } from '../hooks/useProject'
import { useSettings } from '../hooks/useSettings'
import { useStatus } from '../hooks/useStatus'
import { api } from '../services/api'
import { timeAgo } from '../utils/format'

function Segmented({ value, options, onChange, label }) {
  return (
    <div className="inline-flex rounded-control border border-edge bg-ground p-0.5" role="radiogroup" aria-label={label}>
      {options.map(([v, l]) => (
        <button key={v} role="radio" aria-checked={value === v} onClick={() => onChange(v)} className={`relative rounded-full px-3 py-1.5 text-xs font-medium ${value === v ? 'bg-raised text-ink' : 'text-faint hover:text-muted'}`}>
          <span className="relative">{l}</span>
        </button>
      ))}
    </div>
  )
}

export default function Settings() {
  const status = useStatus()
  const { settings, update } = useSettings()
  const { current, refresh } = useProject()
  const [checking, setChecking] = useState(false)
  const [confirmDelete, setConfirmDelete] = useState(false)
  const [deleteError, setDeleteError] = useState(null)
  const [deleting, setDeleting] = useState(false)
  const navigate = useNavigate()
  const ai = status.ai

  const check = async () => {
    setChecking(true)
    await status.refresh(true)
    setChecking(false)
  }

  const remove = async () => {
    setDeleting(true)
    try {
      await api.deleteProject(current.id)
      setConfirmDelete(false)
      await refresh()
      navigate('/')
    } catch (e) {
      setDeleteError(e)
    } finally {
      setDeleting(false)
    }
  }

  return (
    <>
      <PageHeader title="Settings and status" />
      <motion.div variants={stagger} initial="initial" animate="animate" className="grid items-start gap-4 lg:grid-cols-2">
        <Card>
          <CardHeader title="Gemma 4" subtitle="Google Gen AI SDK, called only from the backend" icon={Sparkles}
            action={<Button size="sm" loading={checking} disabled={!ai?.configured} onClick={check}>Check model access</Button>} />
          <div className="space-y-2 p-4 text-sm">
            <Row k="Configured" v={ai?.configured ? <Badge className="bg-positive-bg text-positive-ink">yes</Badge> : <Badge>no API key</Badge>} />
            <Row k="Model" v={<span className="num">{ai?.model || '—'}</span>} />
            <Row k="Provider" v={ai?.provider || '—'} />
            {ai?.last_check && (
              <Row k="Model check" v={ai.last_check.available ? <span className="text-positive">available ({timeAgo(ai.last_check.checked_at)})</span> : <span className="text-negative">{ai.last_check.error}</span>} />
            )}
            {ai?.last_request && <Row k="Last request" v={<span className="num">{`${ai.last_request.status} · ${ai.last_request.task} · ${timeAgo(ai.last_request.at)}`}</span>} />}
            {!ai?.configured && (
              <div className="mt-3 rounded-card border border-accent/30 bg-accent/10 p-3 text-muted">
                <div className="mb-1 font-medium text-ink">Enable Gemma</div>
                <ol className="list-decimal space-y-1 pl-4 text-xs">
                  <li>Create an API key in Google AI Studio.</li>
                  <li>Copy <code className="num">backend/.env.example</code> to <code className="num">backend/.env</code>.</li>
                  <li>Set <code className="num">GEMINI_API_KEY=…</code> (optionally <code className="num">GEMMA_MODEL</code>).</li>
                  <li>Restart the backend. The key never reaches the browser.</li>
                </ol>
              </div>
            )}
            <p className="pt-2 text-xs text-faint">If the configured model fails, requests fail visibly. DevDNA never silently switches to a different model.</p>
          </div>
        </Card>

        <Card>
          <CardHeader title="Backend" icon={Server} action={<Button size="sm" onClick={() => status.refresh()}>Refresh</Button>} />
          <div className="space-y-2 p-4 text-sm">
            <Row k="Status" v={status.backend} />
            <Row k="Version" v={<span className="num">{status.health?.version || '—'}</span>} />
            <Row k="Database" v={status.health?.database || '—'} />
            <Row k="Docker sandbox" v={ai?.docker_sandbox ? 'available' : 'not available (uploaded projects cannot run experiments)'} />
            <Row k="API docs" v={<a className="text-accent hover:underline" href="http://127.0.0.1:8000/docs" target="_blank" rel="noreferrer">OpenAPI /docs</a>} />
          </div>
        </Card>

        <Card>
          <CardHeader title="Privacy and consent" icon={ShieldCheck} />
          <div className="space-y-3 p-4 text-sm text-muted">
            <p>Architecture, prediction, and chat requests offer a preview of the redacted evidence before sending. Multimodal audits and dynamic-scenario synthesis use an explicit checkbox confirmation instead of a full evidence preview.</p>
            <Segmented label="consent" value={settings.consentMode} onChange={(v) => { update({ consentMode: v }); if (v === 'ask') writeStore('devdna.consent', null, 'session') }}
              options={[['ask', 'Ask every time'], ['session', 'Allow remembering per tab']]} />
          </div>
        </Card>

        <Card>
          <CardHeader title="Appearance & Theme" icon={settings.theme === 'bright' ? Sun : Moon} />
          <div className="space-y-3 p-4 text-sm text-muted">
            <p>Switch between the dark instrument theme and the bright theme.</p>
            <Segmented
              label="theme"
              value={settings.theme || 'dark'}
              onChange={(v) => update({ theme: v })}
              options={[
                ['dark', 'Dark (Default)'],
                ['bright', 'Bright'],
              ]}
            />
            <p className="text-xs text-faint">
              Quick shortcut: press <kbd className="num rounded border border-control px-1 py-0.5 text-[11px]">⌘+Shift+D</kbd> or click the sun/moon icon in the sidebar.
            </p>
          </div>
        </Card>

        <Card>
          <CardHeader title="Motion" icon={Cpu} />
          <div className="space-y-3 p-4 text-sm text-muted">
            <p>Animations follow your system setting by default.</p>
            <Segmented label="motion" value={settings.motion} onChange={(v) => update({ motion: v })} options={[['system', 'System'], ['reduced', 'Reduced'], ['full', 'Full']]} />
          </div>
        </Card>

        <Card className="lg:col-span-2" id="formula">
          <CardHeader title="Scores and formulas" icon={Gauge} />
          <div className="grid gap-4 p-4 text-sm text-muted md:grid-cols-2">
            <div>
              <div className="font-medium text-ink">Criticality (0–100)</div>
              <code className="num mt-1 block rounded-card bg-well p-2 text-xs text-muted">100 × (0.5 × blast + 0.3 × betweenness_norm + 0.2 × route_share)</code>
              <ul className="mt-2 space-y-0.5 text-xs">
                <li>blast = non-test dependents ÷ (non-test nodes − 1)</li>
                <li>betweenness_norm = betweenness ÷ max betweenness</li>
                <li>route_share = dependent routes ÷ total routes</li>
              </ul>
              <p className="mt-2 text-xs text-warning">A structural heuristic. It is not a probability of production failure.</p>
            </div>
            <div>
              <div className="font-medium text-ink">Experiment verdicts</div>
              <ul className="mt-1 space-y-1 text-xs">
                <li><b className="text-positive">Confirmed</b>: every definite test prediction matched.</li>
                <li><b className="text-warning">Partially confirmed</b>: some matched, some did not.</li>
                <li><b className="text-negative">Contradicted</b>: no definite prediction matched.</li>
                <li><b className="text-muted">Inconclusive</b>: baseline broken, fault never triggered, or no definite predictions.</li>
                <li><b className="text-warning">Blocked</b>: execution not permitted; nothing ran.</li>
                <li>“May fail” predictions (error handled on path) are reported but never counted as matches.</li>
              </ul>
            </div>
          </div>
        </Card>

        {current && (
          <Card className="border-negative/25 lg:col-span-2">
            <CardHeader title="Danger zone" icon={KeyRound} />
            <div className="flex flex-wrap items-center justify-between gap-3 p-4 text-sm text-muted">
              <span>Delete <b className="text-ink">{current.name}</b> and all of its analyses, predictions and experiments.</span>
              <Button variant="danger" icon={Trash2} onClick={() => setConfirmDelete(true)}>Delete project</Button>
            </div>
          </Card>
        )}
      </motion.div>

      <Modal open={confirmDelete} onClose={() => setConfirmDelete(false)} title="Delete project?" subtitle="This cannot be undone."
        footer={<><Button variant="ghost" onClick={() => setConfirmDelete(false)}>Cancel</Button><Button variant="danger" icon={Trash2} loading={deleting} onClick={remove}>Delete</Button></>}>
        <p className="text-sm text-muted">All stored records for “{current?.name}” will be removed. Uploaded files are deleted; local folders are never modified.</p>
        {deleteError && <div className="mt-3"><ErrorState error={deleteError} /></div>}
      </Modal>
    </>
  )
}

function Row({ k, v }) {
  return (
    <div className="flex items-center justify-between gap-3 border-b border-rule py-1.5 last:border-b-0">
      <span className="text-faint">{k}</span>
      <span className="text-right text-ink">{v}</span>
    </div>
  )
}
