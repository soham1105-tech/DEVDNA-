import { BookOpen, CheckCircle2, ChevronRight, HelpCircle, Info } from 'lucide-react'
import { useState } from 'react'
import { Link } from 'react-router-dom'
import { getTerm } from '../data/glossary'
import { useSettings } from '../hooks/useSettings'
import { buildAnalysisExplanation } from '../utils/analysisExplainer'
import { Badge, Button, Modal } from './ui'

/**
 * Reusable "Explain this analysis" trigger + modal for any DevDNA analysis view.
 * Uses only real analysis data and adapts cleanly between Beginner and Technical modes.
 */
export default function ExplainAnalysisButton({ kind, data, size = 'sm', label = 'Explain this analysis' }) {
  const [open, setOpen] = useState(false)
  const settingsCtx = useSettings()
  const learningMode = settingsCtx?.settings?.learningMode || 'technical'
  const setLearningMode = (mode) => settingsCtx?.update?.({ learningMode: mode })

  const explanation = open ? buildAnalysisExplanation(kind, data, learningMode) : null

  return (
    <>
      <Button
        size={size}
        variant="ghost"
        icon={HelpCircle}
        onClick={() => setOpen(true)}
      >
        {label}
      </Button>

      <Modal
        open={open}
        onClose={() => setOpen(false)}
        title={explanation?.title || 'Explain this analysis'}
        subtitle="Grounded in your current project analysis · switching modes never alters results"
        footer={
          <div className="flex w-full flex-wrap items-center justify-between gap-2">
            <Link
              to="/guide"
              onClick={() => setOpen(false)}
              className="flex items-center gap-1.5 text-xs font-medium text-accent hover:underline"
            >
              <BookOpen className="h-3.5 w-3.5" /> Browse Engineering Guide
            </Link>
            <Button size="sm" onClick={() => setOpen(false)}>Close</Button>
          </div>
        }
      >
        {explanation && (
          <div className="space-y-4 text-sm">
            {/* Header bar: status badge + Beginner/Technical switcher */}
            <div className="flex flex-wrap items-center justify-between gap-2 rounded-card border border-edge bg-ground px-3 py-2">
              <Badge className="bg-accent/15 text-accent">{explanation.badge}</Badge>
              <div className="inline-flex rounded-control border border-edge bg-surface p-0.5 text-xs" role="radiogroup" aria-label="Explanation mode">
                {[['beginner', 'Beginner'], ['technical', 'Technical']].map(([m, text]) => (
                  <button
                    key={m}
                    type="button"
                    role="radio"
                    aria-checked={learningMode === m}
                    onClick={() => setLearningMode(m)}
                    className={`rounded-full px-2.5 py-1 text-xs font-medium transition-colors ${
                      learningMode === m ? 'bg-accent/20 text-accent' : 'text-faint hover:text-muted'
                    }`}
                  >
                    {text}
                  </button>
                ))}
              </div>
            </div>

            <Section title="1. What was calculated" body={explanation.whatCalculated} />
            <Section title={learningMode === 'beginner' ? '2. What it means in simple language' : '2. Technical interpretation'} body={explanation.simpleMeaning} />

            <div>
              <div className="text-xs font-semibold uppercase tracking-wider text-faint">3. Evidence from this project</div>
              <ul className="mt-1.5 space-y-1.5 rounded-card border border-edge bg-ground p-3 text-xs text-muted">
                {explanation.evidence.map((ev, i) => (
                  <li key={i} className="flex items-start gap-2">
                    <Info className="mt-0.5 h-3.5 w-3.5 shrink-0 text-accent" />
                    <span>{ev}</span>
                  </li>
                ))}
              </ul>
            </div>

            <Section title="4. Why it matters" body={explanation.whyItMatters} />

            <div>
              <div className="text-xs font-semibold uppercase tracking-wider text-faint">5. What to investigate next</div>
              <ul className="mt-1.5 space-y-1.5 text-xs text-muted">
                {explanation.nextSteps.map((step, i) => (
                  <li key={i} className="flex items-start gap-2">
                    <ChevronRight className="mt-0.5 h-3.5 w-3.5 shrink-0 text-accent" />
                    <span>{step}</span>
                  </li>
                ))}
              </ul>
            </div>

            {explanation.relatedTerms?.length > 0 && (
              <div className="border-t border-rule pt-3">
                <div className="mb-2 text-xs font-semibold uppercase tracking-wider text-faint">Key concepts referenced</div>
                <div className="flex flex-wrap gap-1.5">
                  {explanation.relatedTerms.map((tid) => {
                    const t = getTerm(tid)
                    if (!t) return null
                    return (
                      <Link
                        key={tid}
                        to={`/guide?term=${tid}`}
                        onClick={() => setOpen(false)}
                        className="rounded-full border border-edge bg-ground px-2.5 py-1 text-xs text-muted transition-colors hover:border-accent hover:text-ink"
                      >
                        {t.shortLabel}
                      </Link>
                    )
                  })}
                </div>
              </div>
            )}

            <div className="flex items-center gap-1.5 text-[11px] text-faint">
              <CheckCircle2 className="h-3.5 w-3.5 text-positive shrink-0" />
              <span>Deterministic explanation grounded strictly in loaded analysis data.</span>
            </div>
          </div>
        )}
      </Modal>
    </>
  )
}

function Section({ title, body }) {
  return (
    <div>
      <div className="text-xs font-semibold uppercase tracking-wider text-faint">{title}</div>
      <p className="mt-1 text-xs leading-relaxed text-ink">{body}</p>
    </div>
  )
}
