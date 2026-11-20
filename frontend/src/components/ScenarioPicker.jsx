import { motion } from 'framer-motion'
import { Info } from 'lucide-react'
import { Link } from 'react-router-dom'
import { SCENARIO_ICONS } from '../utils/constants'
import { shortId } from '../utils/graph'
import TermExplainer from './TermExplainer'

const ENABLE_HINTS = {
  database: 'Add database connection code (SQLite, SQLAlchemy, psycopg) and re-analyse to enable.',
  external_api: 'Add outbound HTTP client calls (httpx, requests, fetch) and re-analyse to enable.',
  env_var: 'Read environment variables (os.environ, os.getenv, process.env) and re-analyse to enable.',
  worker: 'Add a background worker class or task runner and re-analyse to enable.',
}

function criticalityBand(score) {
  if (score >= 60) return { label: 'High', cls: 'text-negative', bar: 'bg-negative' }
  if (score >= 25) return { label: 'Moderate', cls: 'text-warning', bar: 'bg-warning' }
  return { label: 'Low', cls: 'text-accent', bar: 'bg-accent' }
}

/** Choose a supported failure scenario and a real discovered component to target. */
export default function ScenarioPicker({ scenarios = [], scenario, target, onScenario, onTarget }) {
  const current = scenarios.find((s) => s.id === scenario)
  const selectedTarget = current?.targets?.find((t) => t.id === target) || null
  const crit = selectedTarget?.criticality ?? 0
  const band = criticalityBand(crit)

  return (
    <div className="space-y-4">
      <div className="grid gap-2.5 sm:grid-cols-2">
        {scenarios.map((s) => {
          const Icon = SCENARIO_ICONS[s.id]
          const active = s.id === scenario
          const targetCount = s.targets?.length || 0
          const primaryType = s.target_types?.[0]
          const enableHint = ENABLE_HINTS[primaryType] || `Add a ${s.target_types?.join('/')} component and re-analyse to enable.`
          return (
            <motion.button
              key={s.id}
              type="button"
              whileHover={s.supported ? { y: -1 } : undefined}
              whileTap={s.supported ? { scale: 0.99 } : undefined}
              disabled={!s.supported}
              onClick={() => onScenario(s.id)}
              aria-pressed={active}
              className={`relative flex flex-col justify-between rounded-card border p-3.5 text-left transition-colors ${
                !s.supported
                  ? 'cursor-not-allowed border-edge/60 bg-ground/60 opacity-65'
                  : active
                  ? 'border-accent bg-accent/10 shadow-[inset_0_0_0_1px_rgba(74,138,214,0.18)]'
                  : 'border-edge bg-ground hover:border-control'
              }`}
            >
              {/* Selection mark: the 2px accent bar on the card's left edge. */}
              {active && s.supported && <span className="absolute inset-y-2.5 left-0 w-0.5 rounded-r bg-accent" aria-hidden />}
              <div>
                <div className="flex items-start justify-between gap-2">
                  <div className="flex items-center gap-2 min-w-0">
                    {Icon && <Icon className={`h-4 w-4 shrink-0 ${active ? 'text-accent' : 'text-faint'}`} />}
                    <span className="truncate text-sm font-medium text-ink">{s.label}</span>
                  </div>
                  <span
                    className={`num shrink-0 rounded-full px-2 py-0.5 text-[10px] font-medium ${
                      s.supported
                        ? active
                          ? 'bg-accent/20 text-accent'
                          : 'bg-raised text-muted'
                        : 'bg-raised text-faint'
                    }`}
                  >
                    {s.supported ? `${targetCount} ${targetCount === 1 ? 'target' : 'targets'}` : 'Unavailable'}
                  </span>
                </div>
                <p className="mt-1.5 text-xs leading-relaxed text-muted">
                  {s.supported ? s.fault : `No ${s.target_types.join('/')} component discovered in this project.`}
                </p>
              </div>
              {!s.supported && (
                <p className="mt-2 border-t border-rule/70 pt-2 text-[11px] leading-snug text-faint">
                  {enableHint}
                </p>
              )}
            </motion.button>
          )
        })}
      </div>

      {current && (
        <div className="space-y-3">
          <div>
            <label htmlFor="target" className="text-xs font-medium text-muted">
              Target component
            </label>
            <select
              id="target"
              value={target || ''}
              onChange={(e) => onTarget(e.target.value)}
              className="mt-1.5 w-full rounded-control border border-control bg-surface px-3 py-2 text-sm text-ink outline-none focus:border-accent"
            >
              <option value="" disabled>Select a discovered component…</option>
              {current.targets.map((t) => (
                <option key={t.id} value={t.id}>
                  {t.name} ({shortId(t.id)}) · {t.dependents ?? 0} dependents · criticality {t.criticality ?? 0}/100
                </option>
              ))}
            </select>
          </div>

          {selectedTarget && (
            <div className="rounded-card border border-edge bg-ground p-3 text-xs">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <div className="min-w-0">
                  <span className="font-medium text-ink">{selectedTarget.name}</span>
                  <span className="num ml-1.5 text-[11px] text-faint">{shortId(selectedTarget.id)}</span>
                </div>
                <div className="flex items-center gap-1.5">
                  <TermExplainer termId="criticality">
                    <span className="text-muted">Criticality</span>
                  </TermExplainer>
                  <span className={`num font-semibold ${band.cls}`}>{crit}/100</span>
                  <span className="text-[11px] text-faint">({band.label})</span>
                </div>
              </div>

              <div className="mt-2 h-1.5 w-full overflow-hidden rounded-full bg-raised" role="progressbar" aria-valuenow={crit} aria-valuemin={0} aria-valuemax={100} aria-label="Target structural criticality out of 100">
                <div className={`h-full rounded-full ${band.bar}`} style={{ width: `${Math.max(2, Math.min(100, crit))}%` }} />
              </div>

              <div className="mt-2.5 flex flex-wrap items-center justify-between gap-2 text-[11px] text-muted">
                <span className="num">
                  {selectedTarget.dependents ?? 0} dependents
                  {selectedTarget.non_test_total ? ` of ${selectedTarget.non_test_total} components` : ''}
                  {selectedTarget.routes_total ? ` · ${selectedTarget.routes_depending ?? 0}/${selectedTarget.routes_total} routes` : ''}
                  {typeof selectedTarget.tests_covering === 'number' ? ` · ${selectedTarget.tests_covering} covering tests` : ''}
                </span>
                <Link to="/settings#formula" className="text-accent hover:underline">
                  0–100 scale
                </Link>
              </div>

              {(selectedTarget.dependents ?? 0) > 0 && crit < 5 && (
                <div className="mt-2 flex items-start gap-1.5 border-t border-rule pt-2 text-[11px] leading-snug text-faint">
                  <Info className="mt-0.5 h-3.5 w-3.5 shrink-0 text-accent" />
                  <span>
                    Criticality is a 0–100 repository-wide structural score (50% blast share, 30% betweenness, 20% route share). Leaf resources like databases have 0 betweenness and can score low in large repositories even with {selectedTarget.dependents} dependents.
                  </span>
                </div>
              )}
            </div>
          )}
        </div>
      )}
    </div>
  )
}
