import { AnimatePresence, motion } from 'framer-motion'
import { Ban, CheckCircle2, ChevronDown, CircleHelp, Download, FileJson, Lightbulb, MinusCircle, ScrollText, XCircle } from 'lucide-react'
import { useState } from 'react'
import { item, stagger } from '../animations/variants'
import { api, downloadFile } from '../services/api'
import { nextTestSuggestion } from '../utils/format'
import { shortId } from '../utils/graph'
import AIPanel from './AIPanel'
import { InterpretationResult } from './AIRenderers'
import { PredictionBadge } from './ImpactView'
import { Badge, Button, Card, CardHeader, InlineNotice, StatusBadge } from './ui'

const VERDICT = {
  match: { icon: CheckCircle2, cls: 'text-positive', label: 'Match' },
  mismatch: { icon: XCircle, cls: 'text-negative', label: 'Mismatch' },
  uncertain_failed: { icon: CircleHelp, cls: 'text-warning', label: 'Uncertain → failed' },
  uncertain_passed: { icon: CircleHelp, cls: 'text-warning', label: 'Uncertain → passed' },
  excluded: { icon: MinusCircle, cls: 'text-faint', label: 'Excluded' },
}
const OBS = { passed: 'text-positive', failed: 'text-negative', error: 'text-negative', skipped: 'text-faint', not_run: 'text-faint' }

/** The experiment report: predicted outcome vs actual observation, with evidence, logs and limitations. */
export default function PredictionVsReality({ experiment, onUpdate }) {
  const [logs, setLogs] = useState(false)
  const [openRow, setOpenRow] = useState(null)
  const [dl, setDl] = useState(null)
  const c = experiment.comparison || {}
  const m = c.metrics || {}
  const impact = experiment.prediction?.impact
  const fault = experiment.observation?.faulted
  const base = experiment.observation?.baseline

  if (experiment.status === 'blocked') {
    return (
      <Card className="p-5">
        <div className="flex items-center gap-2"><Ban className="h-5 w-5 text-warning" /><StatusBadge status="blocked" /></div>
        <p className="mt-3 text-sm text-muted">{experiment.error}</p>
        <p className="mt-2 text-xs text-faint">The prediction stays a prediction: nothing was executed, so nothing is confirmed or contradicted.</p>
      </Card>
    )
  }
  if (experiment.status === 'failed') {
    return <InlineNotice tone="warn">Execution failed: {experiment.error}</InlineNotice>
  }

  const download = async (fmt) => {
    setDl(fmt)
    try {
      await downloadFile(`/api/experiments/${experiment.id}/report?format=${fmt}`, `devdna-experiment.${fmt}`)
    } finally {
      setDl(null)
    }
  }

  return (
    <motion.div variants={stagger} initial="initial" animate="animate" className="space-y-4">
      <Card className="p-5">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <div className="flex items-center gap-2">
              <StatusBadge status={experiment.status} />
              <Badge className="bg-neutral-bg text-neutral-ink">Experiment executed · {experiment.mode === 'local_harness' ? 'local restricted harness' : experiment.mode}</Badge>
            </div>
            <p className="mt-2 text-sm text-muted">{c.reason}</p>
          </div>
          <div className="flex gap-2">
            <Button size="sm" icon={Download} loading={dl === 'md'} onClick={() => download('md')}>Markdown</Button>
            <Button size="sm" icon={FileJson} loading={dl === 'json'} onClick={() => download('json')}>JSON</Button>
          </div>
        </div>
        <div className="mt-4 grid grid-cols-2 gap-2 sm:grid-cols-5">
          {[
            ['Accuracy', m.accuracy != null ? `${Math.round(m.accuracy * 100)}%` : '—'],
            ['Precision', m.precision != null ? `${Math.round(m.precision * 100)}%` : '—'],
            ['Recall', m.recall != null ? `${Math.round(m.recall * 100)}%` : '—'],
            ['Fault triggered', `${m.fault_triggers ?? 0}×`],
            ['Run time', `${((base?.duration_s || 0) + (fault?.duration_s || 0)).toFixed(1)} s`],
          ].map(([k, v]) => (
            <motion.div variants={item} key={k} className="rounded-card border border-edge bg-ground p-3">
              <div className="text-[11px] text-muted">{k}</div>
              <div className="num mt-1 text-lg font-medium text-ink">{v}</div>
            </motion.div>
          ))}
        </div>
      </Card>

      <div className="grid items-start gap-4 lg:grid-cols-2">
        <Card>
          <CardHeader title="Predicted" subtitle="Static graph analysis, before execution" />
          <div className="space-y-1 p-4 text-sm text-muted">
            <div className="text-ink">{impact?.scenario_label} on <span className="num">{impact?.target?.name}</span></div>
            <div>{impact?.summary?.tests_predicted_fail} tests fail · {impact?.summary?.tests_predicted_may_fail} may fail · {impact?.summary?.tests_predicted_pass} pass</div>
            <div>{impact?.summary?.definite} components definitely affected, {impact?.summary?.possible} possibly</div>
          </div>
        </Card>
        <Card>
          <CardHeader title="Observed" subtitle="Real pytest runs: baseline, then with the injected fault" />
          <div className="space-y-1 p-4 text-sm text-muted">
            <div>Baseline: <span className="num text-positive">{base?.counts?.passed} passed</span>, {base?.counts?.failed + base?.counts?.error} failed</div>
            <div>With fault: <span className="num text-positive">{fault?.counts?.passed} passed</span>, <span className="num text-negative">{fault?.counts?.failed} failed, {fault?.counts?.error} errors</span></div>
            <ul className="mt-1 text-xs text-faint">
              {(fault?.fault_state?.applied || []).map((a) => <li key={a}>• {a}</li>)}
            </ul>
          </div>
        </Card>
      </div>

      <Card>
        <CardHeader title="Prediction vs observation, test by test" subtitle="Click a row for the dependency path and failure message" />
        <ul className="divide-y divide-rule">
          {(c.rows || []).map((r, i) => {
            const V = VERDICT[r.verdict] || VERDICT.excluded
            return (
              <motion.li key={r.nodeid} initial={{ opacity: 0, x: -8 }} animate={{ opacity: 1, x: 0 }} transition={{ delay: 0.03 * i }}>
                <button onClick={() => setOpenRow(openRow === r.nodeid ? null : r.nodeid)} className="flex w-full flex-wrap items-center gap-2 px-4 py-2.5 text-left hover:bg-raised" aria-expanded={openRow === r.nodeid}>
                  <V.icon className={`h-4 w-4 shrink-0 ${V.cls}`} />
                  <span className="num min-w-0 flex-1 truncate text-xs text-ink">{r.nodeid}</span>
                  <PredictionBadge p={r.prediction} />
                  <span className={`num w-16 text-right text-xs ${OBS[r.observed]}`}>{r.observed}</span>
                  <span className={`hidden w-36 text-right text-xs sm:block ${V.cls}`}>{V.label}</span>
                </button>
                <AnimatePresence initial={false}>
                  {openRow === r.nodeid && (
                    <motion.div initial={{ height: 0, opacity: 0 }} animate={{ height: 'auto', opacity: 1 }} exit={{ height: 0, opacity: 0 }} className="overflow-hidden px-4 pb-3">
                      {r.path?.length > 0 && (
                        <div className="text-xs text-muted">Path: <span className="num text-ink">{r.path.map(shortId).join(' → ')}</span></div>
                      )}
                      {r.handled_at && <div className="mt-1 text-xs text-warning">Handled at {r.handled_at.file}:{r.handled_at.line}</div>}
                      {r.why && <div className="mt-1 text-xs text-faint">{r.why}</div>}
                      {r.message ? (
                        <pre className="num mt-2 max-h-40 overflow-auto whitespace-pre-wrap rounded-card bg-well p-2 text-[11px] text-negative-ink">{r.message}</pre>
                      ) : (
                        <div className="mt-1 text-xs text-faint">No failure output (test passed).</div>
                      )}
                      {r.fault_marker && <div className="mt-1 text-[11px] text-accent">The failure message contains the DevDNA fault marker, so it was caused by the injected fault.</div>}
                    </motion.div>
                  )}
                </AnimatePresence>
              </motion.li>
            )
          })}
        </ul>
      </Card>

      <div className="grid items-start gap-4 lg:grid-cols-2">
        <Card className="p-4">
          <div className="flex items-center gap-2 text-sm font-semibold text-ink"><Lightbulb className="h-4 w-4 text-accent" /> Recommended next test</div>
          <p className="mt-2 text-sm text-muted">{experiment.ai_interpretation?.result?.next_test || nextTestSuggestion(c)}</p>
          <div className="mt-4 text-xs font-medium text-ink">Limitations</div>
          <ul className="mt-1 space-y-1 text-xs text-faint">
            {(c.notes || []).map((n) => <li key={n}>• {n}</li>)}
            {(impact?.limitations || []).slice(0, 2).map((n) => <li key={n}>• {n}</li>)}
          </ul>
        </Card>
        <AIPanel
          title="Gemma interpretation"
          subtitle="Explains agreements and discrepancies; never changes the verdict"
          purpose="Experiment interpretation"
          initial={experiment.ai_interpretation}
          fetcher={(body) => api.interpretExperiment(experiment.id, body)}
          render={(r) => <InterpretationResult r={r} />}
          onResult={(d) => onUpdate?.({ ...experiment, ai_interpretation: d })}
        />
      </div>

      <Card>
        <button onClick={() => setLogs((l) => !l)} className="flex w-full items-center justify-between px-4 py-3 text-sm font-medium text-ink" aria-expanded={logs}>
          <span className="flex items-center gap-2"><ScrollText className="h-4 w-4 text-faint" /> Test log (faulted run, redacted)</span>
          <motion.span animate={{ rotate: logs ? 180 : 0 }}><ChevronDown className="h-4 w-4 text-faint" /></motion.span>
        </button>
        <AnimatePresence initial={false}>
          {logs && (
            <motion.pre initial={{ height: 0 }} animate={{ height: 'auto' }} exit={{ height: 0 }} className="num max-h-96 overflow-auto border-t border-rule bg-well p-4 text-[11px] leading-relaxed text-muted">
              {fault?.output || 'Log not included in this view.'}
            </motion.pre>
          )}
        </AnimatePresence>
      </Card>
    </motion.div>
  )
}
