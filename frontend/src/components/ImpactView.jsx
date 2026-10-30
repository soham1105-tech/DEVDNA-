import { AnimatePresence, motion } from 'framer-motion'
import { ChevronRight, FlaskConical, Network, ShieldAlert, ShieldCheck, Sparkles } from 'lucide-react'
import { useState } from 'react'
import { Link } from 'react-router-dom'
import { item, stagger } from '../animations/variants'
import { shortId } from '../utils/graph'
import { Badge, Button, Card, CardHeader, CertaintyBadge, TypeBadge } from './ui'

const PRED = {
  fail: { label: 'Predicted fail', cls: 'bg-negative-bg text-negative-ink' },
  may_fail: { label: 'May fail', cls: 'bg-warning-bg text-warning-ink' },
  pass: { label: 'Predicted pass', cls: 'bg-positive-bg text-positive-ink' },
}
export const PredictionBadge = ({ p }) => <Badge className={PRED[p]?.cls}>{PRED[p]?.label || p}</Badge>

function PathChips({ path }) {
  return (
    <div className="mt-1.5 flex flex-wrap items-center gap-1">
      {path.map((p, i) => (
        <span key={i} className="flex items-center gap-1">
          {i > 0 && <ChevronRight className="h-3 w-3 text-faint" />}
          <span className="num rounded bg-raised px-1.5 py-0.5 text-[11px] text-muted">{shortId(p)}</span>
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
        <motion.div variants={item} className="space-y-3 rounded-card border border-accent/30 bg-accent/10 p-4">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <div className="flex items-center gap-2">
              <Sparkles className="h-4 w-4 text-accent" />
              <div>
                <h3 className="flex items-center gap-2 text-sm font-semibold text-ink">
                  Deterministic impact assessment
                  <span className="num rounded-full bg-accent/15 px-2 py-0.5 text-[11px] font-medium text-accent">
                    Heuristic score: {impact.neuro_symbolic_simulation.cascading_risk_score}/100
                  </span>
                </h3>
                <p className="text-xs text-muted">Heuristic score, not a failure probability; no AI model used for this score</p>
              </div>
            </div>
            <Badge variant={impact.neuro_symbolic_simulation.blast_radius_assessment === 'CRITICAL' ? 'danger' : 'warning'}
              className={impact.neuro_symbolic_simulation.blast_radius_assessment === 'CRITICAL' ? 'bg-negative-bg text-negative-ink' : 'bg-warning-bg text-warning-ink'}>
              {impact.neuro_symbolic_simulation.blast_radius_assessment}
            </Badge>
          </div>

          <p className="text-xs leading-relaxed text-muted">
            {impact.neuro_symbolic_simulation.semantic_summary}
          </p>

          <div className="grid grid-cols-1 gap-2 border-t border-accent/20 pt-1 text-xs md:grid-cols-2">
            <div>
              <div className="mb-1 flex items-center gap-1 text-xs font-medium text-warning">
                <ShieldAlert className="h-3 w-3" /> Runtime Failure Vulnerabilities
              </div>
              <ul className="list-inside list-disc space-y-0.5 text-muted">
                {impact.neuro_symbolic_simulation.runtime_vulnerabilities?.map((v, i) => (
                  <li key={i}>{v}</li>
                ))}
              </ul>
            </div>
            <div>
              <div className="mb-1 flex items-center gap-1 text-xs font-medium text-accent">
                <Network className="h-3 w-3" /> Test Mapping Analysis
              </div>
              <ul className="list-inside list-disc space-y-0.5 text-muted">
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
          ['Definitely affected', s.definite, 'text-negative'],
          ['Possibly affected', s.possible, 'text-warning'],
          ['Routes affected', `${s.routes_affected}/${s.routes_total}`, 'text-ink'],
          ['Tests predicted to fail', `${s.tests_predicted_fail} (+${s.tests_predicted_may_fail} may)`, 'text-ink'],
        ].map(([k, v, c]) => (
          <div key={k} className="rounded-card border border-edge bg-ground p-3">
            <div className="text-[11px] text-muted">{k}</div>
            <div className={`num mt-1 text-lg font-medium ${c}`}>{v}</div>
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
        <ul className="max-h-[420px] divide-y divide-rule overflow-y-auto">
          {affected.slice(0, compact ? 12 : 200).map((a) => (
            <li key={a.id} className="px-4 py-2.5">
              <button className="flex w-full items-center gap-2 text-left" onClick={() => setExpanded(expanded === a.id ? null : a.id)} aria-expanded={expanded === a.id}>
                <CertaintyBadge certainty={a.certainty} />
                <TypeBadge type={a.type} />
                <span className="num min-w-0 flex-1 truncate text-xs text-ink">{shortId(a.id)}</span>
                <span className="shrink-0 text-[11px] text-faint">{a.distance} hop{a.distance === 1 ? '' : 's'}</span>
              </button>
              <AnimatePresence initial={false}>
                {expanded === a.id && (
                  <motion.div initial={{ height: 0, opacity: 0 }} animate={{ height: 'auto', opacity: 1 }} exit={{ height: 0, opacity: 0 }} className="overflow-hidden">
                    <PathChips path={a.path} />
                    {a.handled_at && (
                      <div className="mt-1.5 flex items-center gap-1.5 text-[11px] text-warning">
                        <ShieldCheck className="h-3 w-3" /> Error handled at {a.handled_at.file}:{a.handled_at.line} (catches {a.handled_at.handles.join(', ')}). Outcome uncertain.
                      </div>
                    )}
                  </motion.div>
                )}
              </AnimatePresence>
            </li>
          ))}
          {affected.length === 0 && <li className="p-4 text-sm text-muted">No components depend on this one.</li>}
        </ul>
      </Card>

      <Card>
        <CardHeader title="Per-test predictions" subtitle="What an experiment will check" icon={FlaskConical} />
        <ul className="divide-y divide-rule">
          {impact.tests.map((t) => (
            <li key={t.id} className="flex flex-wrap items-center gap-2 px-4 py-2">
              <PredictionBadge p={t.prediction} />
              <span className="num min-w-0 flex-1 truncate text-xs text-muted">{t.nodeid}</span>
            </li>
          ))}
          {impact.tests.length === 0 && <li className="p-4 text-sm text-muted">No tests discovered, so this prediction cannot be validated by an experiment.</li>}
        </ul>
      </Card>

      {!compact && (
        <Card className="p-4 text-xs text-muted">
          <div className="mb-1 font-medium text-ink">Method</div>
          <p>{impact.method}</p>
          <div className="mb-1 mt-3 font-medium text-ink">Limitations</div>
          <ul className="list-disc space-y-0.5 pl-4">
            {impact.limitations.map((l) => <li key={l}>{l}</li>)}
          </ul>
        </Card>
      )}
    </motion.div>
  )
}
