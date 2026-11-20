import { BookOpen, Info } from 'lucide-react'
import { useState } from 'react'
import { Link } from 'react-router-dom'
import { getTerm } from '../data/glossary'
import { useSettings } from '../hooks/useSettings'
import { Badge, Modal } from './ui'

/**
 * Accessible click-to-open contextual explainer for a technical term.
 * Hover provides a brief title hint; click or Enter/Space opens the full panel.
 */
export default function TermExplainer({ termId, children, className = '' }) {
  const [open, setOpen] = useState(false)
  const [activeId, setActiveId] = useState(termId)
  const settingsCtx = useSettings()
  const learningMode = settingsCtx?.settings?.learningMode || 'technical'
  const setLearningMode = (mode) => settingsCtx?.update?.({ learningMode: mode })

  const initialTerm = getTerm(termId)
  const activeTerm = getTerm(activeId) || initialTerm
  if (!initialTerm) return children || null

  const openModal = (e) => {
    e?.stopPropagation?.()
    setActiveId(termId)
    setOpen(true)
  }

  return (
    <>
      <span className={`inline-flex items-center gap-1 ${className}`}>
        {children}
        <button
          type="button"
          onClick={openModal}
          aria-label={`Explain ${initialTerm.shortLabel}`}
          aria-expanded={open}
          title={`${initialTerm.term}: ${initialTerm.simple}`}
          className="inline-flex h-4 w-4 shrink-0 items-center justify-center rounded-full text-faint transition-colors hover:bg-raised hover:text-accent focus:outline-none focus:ring-1 focus:ring-accent"
        >
          <Info className="h-3.5 w-3.5" />
        </button>
      </span>

      <Modal
        open={open}
        onClose={() => setOpen(false)}
        title={activeTerm.term}
        subtitle={learningMode === 'beginner' ? 'Beginner explanation · plain English' : 'Technical definition · formulas & implementation'}
        footer={
          <div className="flex w-full flex-wrap items-center justify-between gap-2">
            <Link
              to={`/guide?term=${activeTerm.id}`}
              onClick={() => setOpen(false)}
              className="flex items-center gap-1.5 text-xs font-medium text-accent hover:underline"
            >
              <BookOpen className="h-3.5 w-3.5" /> Open in Engineering Guide
            </Link>
            <button
              type="button"
              onClick={() => setOpen(false)}
              className="rounded-control border border-control px-3 py-1.5 text-xs font-medium text-ink hover:bg-raised"
            >
              Close
            </button>
          </div>
        }
      >
        <div className="space-y-4 text-sm">
          {/* Learning mode toggle */}
          <div className="flex items-center justify-between gap-2 rounded-card border border-edge bg-ground px-3 py-2">
            <span className="text-xs text-muted">Explanation level</span>
            <div className="inline-flex rounded-control border border-edge bg-surface p-0.5 text-xs" role="radiogroup" aria-label="Explanation mode">
              {[['beginner', 'Beginner'], ['technical', 'Technical']].map(([m, label]) => (
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
                  {label}
                </button>
              ))}
            </div>
          </div>

          {learningMode === 'beginner' ? (
            <div className="space-y-3">
              <div>
                <div className="text-xs font-semibold uppercase tracking-wider text-faint">In plain English</div>
                <p className="mt-1 leading-relaxed text-ink">{activeTerm.simple}</p>
              </div>
              {activeTerm.analogy && (
                <div className="rounded-card border border-edge bg-ground p-3 text-xs">
                  <div className="font-medium text-accent">Everyday analogy</div>
                  <p className="mt-1 leading-relaxed text-muted">{activeTerm.analogy}</p>
                </div>
              )}
            </div>
          ) : (
            <div className="space-y-3">
              <div>
                <div className="text-xs font-semibold uppercase tracking-wider text-faint">Technical definition</div>
                <p className="mt-1 leading-relaxed text-ink">{activeTerm.technical}</p>
              </div>
              {activeTerm.formula && (
                <div className="rounded-card border border-edge bg-well p-3">
                  <div className="text-[11px] font-medium text-faint">Implemented formula</div>
                  <code className="num mt-1 block text-xs text-accent">{activeTerm.formula}</code>
                </div>
              )}
            </div>
          )}

          {activeTerm.example && (
            <div>
              <div className="text-xs font-semibold uppercase tracking-wider text-faint">Codebase example</div>
              <p className="mt-1 text-xs leading-relaxed text-muted">{activeTerm.example}</p>
            </div>
          )}

          <div>
            <div className="text-xs font-semibold uppercase tracking-wider text-faint">Why it matters</div>
            <p className="mt-1 text-xs leading-relaxed text-muted">{activeTerm.whyItMatters}</p>
          </div>

          {activeTerm.related?.length > 0 && (
            <div className="border-t border-rule pt-3">
              <div className="mb-2 text-xs font-semibold uppercase tracking-wider text-faint">Related concepts</div>
              <div className="flex flex-wrap gap-1.5">
                {activeTerm.related.map((relId) => {
                  const rel = getTerm(relId)
                  if (!rel) return null
                  return (
                    <button
                      key={relId}
                      type="button"
                      onClick={() => setActiveId(relId)}
                      className="rounded-full border border-edge bg-ground px-2.5 py-1 text-xs text-muted transition-colors hover:border-accent hover:text-ink"
                    >
                      {rel.shortLabel}
                    </button>
                  )
                })}
              </div>
            </div>
          )}

          <div className="text-[11px] text-faint">
            <Badge className="mr-1.5 bg-raised text-muted">Read-only</Badge>
            Switching explanation mode changes wording only—never your computed analysis results.
          </div>
        </div>
      </Modal>
    </>
  )
}
