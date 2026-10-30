import { AnimatePresence, motion } from 'framer-motion'
import { Check, Loader2 } from 'lucide-react'

const STAGES = [
  'Scanning files',
  'Parsing Python sources',
  'Indexing symbols',
  'Resolving routes and application wiring',
  'Resolving calls and resource usage',
  'Mapping tests to components',
  'Reading dependency manifests',
  'Computing graph metrics',
  'Generating findings',
]

/** Live view of a real analysis run: stages come from the backend as they happen. */
export default function AnalysisProgress({ run, upload }) {
  const done = new Set((run?.stages || []).map((s) => s.stage))
  const completed = run?.status === 'completed'
  const progress = completed ? 1 : run?.progress || 0
  const summary = run?.summary || {}
  return (
    <div>
      {upload > 0 && upload < 1 && !run && <div className="num mb-3 text-xs text-muted">Uploading… {Math.round(upload * 100)}%</div>}
      <div className="h-1 overflow-hidden rounded-full bg-raised">
        <motion.div className="h-full rounded-full bg-accent" animate={{ width: `${Math.max(4, progress * 100)}%` }} transition={{ duration: 0.4 }} />
      </div>
      <ul className="mt-4 space-y-1.5">
        {STAGES.map((stage, i) => {
          const isDone = completed || done.has(stage) && run?.stage !== stage
          const active = !completed && run?.stage === stage
          const reached = isDone || active
          return (
            <motion.li
              key={stage}
              initial={{ opacity: 0, x: -6 }}
              animate={{ opacity: reached ? 1 : 0.4, x: 0 }}
              transition={{ delay: i * 0.03 }}
              className="flex items-center gap-2.5 text-sm"
            >
              <span className={`flex h-5 w-5 items-center justify-center rounded-full border ${isDone ? 'border-positive/50 text-positive' : active ? 'border-accent text-accent' : 'border-control text-faint'}`}>
                {isDone ? <Check className="h-3 w-3" /> : active ? <Loader2 className="h-3 w-3 animate-spin" /> : <span className="h-1 w-1 rounded-full bg-current" />}
              </span>
              <span className={reached ? 'text-ink' : 'text-faint'}>{stage}</span>
            </motion.li>
          )
        })}
      </ul>
      <AnimatePresence>
        {completed && summary.scan && (
          <motion.div initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }} className="mt-4 grid grid-cols-3 gap-2 text-center">
            {[
              ['Files', summary.scan.file_count],
              ['Components', Object.values(summary.component_counts || {}).reduce((a, b) => a + b, 0)],
              ['Findings', summary.findings],
            ].map(([k, v]) => (
              <div key={k} className="rounded-card border border-edge bg-ground py-2">
                <div className="num text-lg font-medium text-ink">{v}</div>
                <div className="text-[11px] text-faint">{k}</div>
              </div>
            ))}
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  )
}
