import { AnimatePresence, motion } from 'framer-motion'
import { ChevronRight, FlaskConical, Network, ShieldAlert, ShieldCheck, Sparkles } from 'lucide-react'
import { useState } from 'react'
import { Link } from 'react-router-dom'
import { item, stagger } from '../animations/variants'
import { shortId } from '../utils/graph'
import { Badge, Button, Card, CardHeader, CertaintyBadge, TypeBadge } from './ui'

const PRED = {
  fail: { label: 'Predicted fail', cls: 'text-rose-300 bg-rose-500/10 border-rose-500/25' },
  may_fail: { label: 'May fail', cls: 'text-amber-200 bg-amber-500/10 border-amber-500/25' },
  pass: { label: 'Predicted pass', cls: 'text-emerald-300 bg-emerald-500/10 border-emerald-500/25' },
}
export const PredictionBadge = ({ p }) => <Badge className={PRED[p]?.cls}>{PRED[p]?.label || p}</Badge>

function PathChips({ path }) {
  return (
    <div className="mt-1.5 flex flex-wrap items-center gap-1">
      {path.map((p, i) => (
        <span key={i} className="flex items-center gap-1">
          {i > 0 && <ChevronRight className="h-3 w-3 text-ink-500" />}
          <span className="rounded bg-ink-700/80 px-1.5 py-0.5 font-mono text-[10px] text-ink-200">{shortId(p)}</span>
        </span>
      ))}
    </div>
  )
}

/** Displays deterministic graph impact and its heuristic risk score. */
export default function ImpactView({ prediction, compact = false }) {
  const impact = prediction.impact
  const s = impact.summary
  const [expanded, setExpanded] = useState(null)
  const affected = impact.affected.filter((a) => a.type !== 'module')
  return (
    <motion.div variants={stagger} initial="initial" animate="animate" className="space-y-4">
      {impact.neuro_symbolic_simulation && (
        <motion.div variants={item} className="p-4 rounded-xl border border-violet-500/30 bg-gradient-to-r from-violet-950/30 to-ink-900/60 shadow-lg space-y-3">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <div className="flex items-center gap-2">
              <span className="flex h-7 w-7 items-center justify-center rounded-lg bg-violet-500/20 text-violet-300">
                <Sparkles className="h-4 w-4" />
              </span>
              <div>
                <h3 className="text-sm font-semibold text-ink-100 flex items-center gap-2">
                  Deterministic impact assessment
                  <span className="px-2 py-0.5 rounded text-[10px] font-bold tracking-wider bg-violet-500/20 text-violet-300 border border-violet-500/30">
                    Heuristic score: {impact.neuro_symbolic_simulation.cascading_risk_score}/100
                  </span>
                </h3>
                <p className="text-xs text-ink-400">Heuristic score, not a failure probability; no AI model used for this score</p>
              </div>
            </div>
            <Badge variant={impact.neuro_symbolic_simulation.blast_radius_assessment === 'CRITICAL' ? 'danger' : 'warning'}>
              {impact.neuro_symbolic_simulation.blast_radius_assessment}
            </Badge>
          </div>

          <p className="text-xs text-ink-200 leading-relaxed">
            {impact.neuro_symbolic_simulation.semantic_summary}
          </p>

          <div className="grid grid-cols-1 md:grid-cols-2 gap-2 pt-1 border-t border-white/[0.06] text-xs">
            <div>
              <div className="text-[11px] font-semibold text-amber-300 flex items-center gap-1 mb-1">
                <ShieldAlert className="h-3 w-3" /> Runtime Failure Vulnerabilities
              </div>
              <ul className="space-y-0.5 text-ink-300 list-disc list-inside">
                {impact.neuro_symbolic_simulation.runtime_vulnerabilities?.map((v, i) => (
                  <li key={i}>{v}</li>
                ))}
              </ul>
            </div>
            <div>
              <div className="text-[11px] font-semibold text-cyan-300 flex items-center gap-1 mb-1">
                <Network className="h-3 w-3" /> Test Mapping Analysis
              </div>
              <ul className="space-y-0.5 text-ink-300 list-disc list-inside">
                {impact.neuro_symbolic_simulation.test_coverage_gap?.map((g, i) => (
                  <li key={i}>{g}</li>
                ))}
              </ul>
            </div>
          </div>
        </motion.div>
      )}

      <motion.div variants={item} className="grid grid-cols-2 gap-2 sm:grid-cols-4">
        {[
          ['Definitely affected', s.definite, 'text-rose-300'],
          ['Possibly affected', s.possible, 'text-amber-200'],
          ['Routes affected', `${s.routes_affected}/${s.routes_total}`, 'text-cyan-200'],
          ['Tests predicted to fail', `${s.tests_predicted_fail} (+${s.tests_predicted_may_fail} may)`, 'text-violet-200'],
        ].map(([k, v, c]) => (
          <div key={k} className="rounded-lg border border-white/[0.06] bg-ink-900/60 p-3">
            <div className="text-[10px] uppercase tracking-wider text-ink-400">{k}</div>
            <div className={`mt-1 text-lg font-semibold tabular-nums ${c}`}>{v}</div>
          </div>
        ))}
      </motion.div>

      <Card>
        <CardHeader
          title="Affected components"
          subtitle="Reverse graph traversal from the failing component; paths show why"
          icon={Network}
          action={
            <Link to={`/architecture?prediction=${prediction.id}`}>
              <Button size="sm" icon={Network}>View in graph</Button>
            </Link>
          }
        />
        <ul className="max-h-[420px] divide-y divide-white/[0.04] overflow-y-auto">
          {affected.slice(0, compact ? 12 : 200).map((a) => (
            <li key={a.id} className="px-4 py-2.5">
              <button className="flex w-full items-center gap-2 text-left" onClick={() => setExpanded(expanded === a.id ? null : a.id)} aria-expanded={expanded === a.id}>
                <CertaintyBadge certainty={a.certainty} />
                <TypeBadge type={a.type} />
                <span className="min-w-0 flex-1 truncate font-mono text-xs text-ink-100">{shortId(a.id)}</span>
                <span className="shrink-0 text-[11px] text-ink-400">{a.distance} hop{a.distance === 1 ? '' : 's'}</span>
              </button>
              <AnimatePresence initial={false}>
                {expanded === a.id && (
                  <motion.div initial={{ height: 0, opacity: 0 }} animate={{ height: 'auto', opacity: 1 }} exit={{ height: 0, opacity: 0 }} className="overflow-hidden">
                    <PathChips path={a.path} />
                    {a.handled_at && (
                      <div className="mt-1.5 flex items-center gap-1.5 text-[11px] text-amber-200/90">
                        <ShieldCheck className="h-3 w-3" /> Error handled at {a.handled_at.file}:{a.handled_at.line} (catches {a.handled_at.handles.join(', ')}). Outcome uncertain.
                      </div>
                    )}
                  </motion.div>
                )}
              </AnimatePresence>
            </li>
          ))}
          {affected.length === 0 && <li className="p-4 text-sm text-ink-300">No components depend on this one.</li>}
        </ul>
      </Card>

      <Card>
        <CardHeader title="Per-test predictions" subtitle="What an experiment will check" icon={FlaskConical} />
        <ul className="divide-y divide-white/[0.04]">
          {impact.tests.map((t) => (
            <li key={t.id} className="flex flex-wrap items-center gap-2 px-4 py-2">
              <PredictionBadge p={t.prediction} />
              <span className="min-w-0 flex-1 truncate font-mono text-xs text-ink-200">{t.nodeid}</span>
            </li>
          ))}
          {impact.tests.length === 0 && <li className="p-4 text-sm text-ink-300">No tests discovered, so this prediction cannot be validated by an experiment.</li>}
        </ul>
      </Card>

      {!compact && (
        <Card className="p-4 text-xs text-ink-300">
          <div className="mb-1 font-medium text-ink-200">Method</div>
          <p>{impact.method}</p>
          <div className="mb-1 mt-3 font-medium text-ink-200">Limitations</div>
          <ul className="list-disc space-y-0.5 pl-4">
            {impact.limitations.map((l) => <li key={l}>{l}</li>)}
          </ul>
        </Card>
      )}
    </motion.div>
  )
}
