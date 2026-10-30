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
    <div className="flex rounded-lg border border-white/[0.07] bg-ink-900 p-0.5" role="radiogroup" aria-label={label}>
      {options.map(([v, l]) => (
        <button key={v} role="radio" aria-checked={value === v} onClick={() => onChange(v)} className={`relative rounded-md px-3 py-1.5 text-xs font-medium ${value === v ? 'text-ink-100' : 'text-ink-400'}`}>
          {value === v && <motion.span layoutId={`seg-${label}`} className="absolute inset-0 rounded-md bg-ink-700" />}
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
      <PageHeader eyebrow="Settings" title="Settings and status" />
      <motion.div variants={stagger} initial="initial" animate="animate" className="grid gap-4 lg:grid-cols-2">
        <Card>
          <CardHeader title="Gemma 4" subtitle="Google Gen AI SDK, called only from the backend" icon={Sparkles}
            action={<Button size="sm" loading={checking} disabled={!ai?.configured} onClick={check}>Check model access</Button>} />
          <div className="space-y-2 p-4 text-sm">
            <Row k="Configured" v={ai?.configured ? <Badge className="border-emerald-400/25 bg-emerald-400/10 text-emerald-200">yes</Badge> : <Badge>no API key</Badge>} />
            <Row k="Model" v={<span className="font-mono">{ai?.model || '—'}</span>} />
            <Row k="Provider" v={ai?.provider || '—'} />
            {ai?.last_check && (
              <Row k="Model check" v={ai.last_check.available ? <span className="text-emerald-300">available ({timeAgo(ai.last_check.checked_at)})</span> : <span className="text-rose-300">{ai.last_check.error}</span>} />
            )}
            {ai?.last_request && <Row k="Last request" v={`${ai.last_request.status} · ${ai.last_request.task} · ${timeAgo(ai.last_request.at)}`} />}
            {!ai?.configured && (
              <div className="mt-3 rounded-lg border border-violet-400/20 bg-violet-400/[0.05] p-3 text-ink-200">
                <div className="mb-1 font-medium text-violet-100">Enable Gemma</div>
                <ol className="list-decimal space-y-1 pl-4 text-xs text-ink-300">
                  <li>Create an API key in Google AI Studio.</li>
                  <li>Copy <code className="font-mono">backend/.env.example</code> to <code className="font-mono">backend/.env</code>.</li>
                  <li>Set <code className="font-mono">GEMINI_API_KEY=…</code> (optionally <code className="font-mono">GEMMA_MODEL</code>).</li>
                  <li>Restart the backend. The key never reaches the browser.</li>
                </ol>
              </div>
            )}
            <p className="pt-2 text-xs text-ink-400">If the configured model fails, requests fail visibly. DevDNA never silently switches to a different model.</p>
          </div>
        </Card>

        <Card>
          <CardHeader title="Backend" icon={Server} action={<Button size="sm" onClick={() => status.refresh()}>Refresh</Button>} />
          <div className="space-y-2 p-4 text-sm">
            <Row k="Status" v={status.backend} />
            <Row k="Version" v={status.health?.version || '—'} />
            <Row k="Database" v={status.health?.database || '—'} />
            <Row k="Docker sandbox" v={ai?.docker_sandbox ? 'available' : 'not available (uploaded projects cannot run experiments)'} />
            <Row k="API docs" v={<a className="text-violet-300 hover:underline" href="http://127.0.0.1:8000/docs" target="_blank" rel="noreferrer">OpenAPI /docs</a>} />
          </div>
        </Card>

        <Card>
          <CardHeader title="Privacy and consent" icon={ShieldCheck} />
          <div className="space-y-3 p-4 text-sm text-ink-300">
            <p>Architecture, prediction, and chat requests offer a preview of the redacted evidence before sending. Multimodal audits and dynamic-scenario synthesis use an explicit checkbox confirmation instead of a full evidence preview.</p>
            <Segmented label="consent" value={settings.consentMode} onChange={(v) => { update({ consentMode: v }); if (v === 'ask') writeStore('devdna.consent', null, 'session') }}
              options={[['ask', 'Ask every time'], ['session', 'Allow remembering per tab']]} />
          </div>
        </Card>

        <Card>
          <CardHeader title="Appearance & Theme" icon={settings.theme === 'bright' ? Sun : Moon} />
          <div className="space-y-3 p-4 text-sm text-ink-300">
            <p>Switch between dark sci-fi twin mode and clean high-contrast bright mode.</p>
            <Segmented
              label="theme"
              value={settings.theme || 'dark'}
              onChange={(v) => update({ theme: v })}
              options={[
                ['dark', 'Dark (Default)'],
                ['bright', 'Bright'],
              ]}
            />
            <p className="text-xs text-ink-400">
              Quick shortcut: press <kbd className="rounded border border-white/10 bg-ink-800 px-1 py-0.5 font-mono text-[10px] text-ink-200">⌘+Shift+D</kbd> or click the sun/moon icon in the sidebar.
            </p>
          </div>
        </Card>

        <Card>
          <CardHeader title="Motion" icon={Cpu} />
          <div className="space-y-3 p-4 text-sm text-ink-300">
            <p>Animations follow your system setting by default.</p>
            <Segmented label="motion" value={settings.motion} onChange={(v) => update({ motion: v })} options={[['system', 'System'], ['reduced', 'Reduced'], ['full', 'Full']]} />
          </div>
        </Card>

        <Card className="lg:col-span-2" id="formula">
          <CardHeader title="Scores and formulas" icon={Gauge} />
          <div className="grid gap-4 p-4 text-sm text-ink-300 md:grid-cols-2">
            <div>
              <div className="font-medium text-ink-100">Criticality (0–100)</div>
              <code className="mt-1 block rounded bg-ink-900 p-2 font-mono text-xs text-ink-200">100 × (0.5 × blast + 0.3 × betweenness_norm + 0.2 × route_share)</code>
              <ul className="mt-2 space-y-0.5 text-xs">
                <li>blast = non-test dependents ÷ (non-test nodes − 1)</li>
                <li>betweenness_norm = betweenness ÷ max betweenness</li>
                <li>route_share = dependent routes ÷ total routes</li>
              </ul>
              <p className="mt-2 text-xs text-amber-200/80">A structural heuristic. It is not a probability of production failure.</p>
            </div>
            <div>
              <div className="font-medium text-ink-100">Experiment verdicts</div>
              <ul className="mt-1 space-y-1 text-xs">
                <li><b className="text-emerald-300">Confirmed</b>: every definite test prediction matched.</li>
                <li><b className="text-amber-300">Partially confirmed</b>: some matched, some did not.</li>
                <li><b className="text-rose-300">Contradicted</b>: no definite prediction matched.</li>
                <li><b className="text-ink-200">Inconclusive</b>: baseline broken, fault never triggered, or no definite predictions.</li>
                <li><b className="text-orange-300">Blocked</b>: execution not permitted; nothing ran.</li>
                <li>“May fail” predictions (error handled on path) are reported but never counted as matches.</li>
              </ul>
            </div>
          </div>
        </Card>

        {current && (
          <Card className="lg:col-span-2">
            <CardHeader title="Danger zone" icon={KeyRound} />
            <div className="flex flex-wrap items-center justify-between gap-3 p-4 text-sm text-ink-300">
              <span>Delete <b className="text-ink-100">{current.name}</b> and all of its analyses, predictions and experiments.</span>
              <Button variant="danger" icon={Trash2} onClick={() => setConfirmDelete(true)}>Delete project</Button>
            </div>
          </Card>
        )}
      </motion.div>

      <Modal open={confirmDelete} onClose={() => setConfirmDelete(false)} title="Delete project?" subtitle="This cannot be undone."
        footer={<><Button variant="ghost" onClick={() => setConfirmDelete(false)}>Cancel</Button><Button variant="danger" icon={Trash2} loading={deleting} onClick={remove}>Delete</Button></>}>
        <p className="text-sm text-ink-300">All stored records for “{current?.name}” will be removed. Uploaded files are deleted; local folders are never modified.</p>
        {deleteError && <div className="mt-3"><ErrorState error={deleteError} /></div>}
      </Modal>
    </>
  )
}

function Row({ k, v }) {
  return (
    <div className="flex items-center justify-between gap-3">
      <span className="text-ink-400">{k}</span>
      <span className="text-right text-ink-100">{v}</span>
    </div>
  )
}
