import { BookOpen, ChevronRight, Search } from 'lucide-react'
import { useEffect, useMemo, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { Badge, Card, EmptyState, PageHeader } from '../components/ui'
import { GLOSSARY_CATEGORIES, getTerm, searchGlossary } from '../data/glossary'
import { useSettings } from '../hooks/useSettings'

export default function EngineeringGuide() {
  const [params, setParams] = useSearchParams()
  const settingsCtx = useSettings()
  const learningMode = settingsCtx?.settings?.learningMode || 'technical'
  const setLearningMode = (mode) => settingsCtx?.update?.({ learningMode: mode })

  const [query, setQuery] = useState('')
  const [category, setCategory] = useState('all')
  const [selectedId, setSelectedId] = useState(params.get('term') || 'ast')

  useEffect(() => {
    const t = params.get('term')
    if (t && getTerm(t)) setSelectedId(t)
  }, [params])

  const results = useMemo(() => searchGlossary(query, category), [query, category])
  const selected = getTerm(selectedId) || results[0] || null
  const categoryLabel = (id) => GLOSSARY_CATEGORIES.find((c) => c.id === id)?.label || id

  const selectTerm = (id) => {
    setSelectedId(id)
    setParams({ term: id })
  }

  return (
    <>
      <PageHeader
        title="Engineering Guide"
        subtitle="Searchable reference for DevDNA’s static analysis, dependency graph, risk metrics, fault-injection validation, and grounded AI concepts."
        actions={
          <div className="inline-flex items-center rounded-control border border-edge bg-surface p-0.5 text-xs" role="radiogroup" aria-label="Explanation mode">
            {[['beginner', 'Beginner'], ['technical', 'Technical']].map(([m, label]) => (
              <button
                key={m}
                type="button"
                role="radio"
                aria-checked={learningMode === m}
                onClick={() => setLearningMode(m)}
                className={`rounded-full px-3 py-1.5 text-xs font-medium transition-colors ${
                  learningMode === m ? 'bg-accent/20 text-accent' : 'text-faint hover:text-muted'
                }`}
              >
                {label}
              </button>
            ))}
          </div>
        }
      />

      {/* Search + Category filter bar */}
      <Card className="mb-4 p-4 space-y-3">
        <div className="relative">
          <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-faint" />
          <input
            type="search"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Search terms (e.g. AST, Reverse BFS, criticality, fault injection, precision)..."
            aria-label="Search engineering guide"
            className="w-full rounded-control border border-control bg-ground py-2 pl-9 pr-3 text-sm text-ink outline-none placeholder:text-faint focus:border-accent"
          />
        </div>
        <div className="flex flex-wrap gap-1.5" role="tablist" aria-label="Glossary categories">
          <button
            type="button"
            role="tab"
            aria-selected={category === 'all'}
            onClick={() => setCategory('all')}
            className={`rounded-full px-3 py-1 text-xs font-medium transition-colors ${
              category === 'all' ? 'bg-accent text-white' : 'border border-edge bg-ground text-muted hover:text-ink'
            }`}
          >
            All ({searchGlossary(query, 'all').length})
          </button>
          {GLOSSARY_CATEGORIES.map((c) => (
            <button
              key={c.id}
              type="button"
              role="tab"
              aria-selected={category === c.id}
              onClick={() => setCategory(c.id)}
              className={`rounded-full px-3 py-1 text-xs font-medium transition-colors ${
                category === c.id ? 'bg-accent text-white' : 'border border-edge bg-ground text-muted hover:text-ink'
              }`}
            >
              {c.label}
            </button>
          ))}
        </div>
      </Card>

      {results.length === 0 ? (
        <EmptyState
          icon={BookOpen}
          title="No matching engineering terms"
          description={`No terms matched "${query}". Try searching for AST, blast radius, criticality, baseline, or precision.`}
        />
      ) : (
        <div className="grid gap-4 lg:grid-cols-[360px_1fr] items-start">
          {/* Term List */}
          <Card className="overflow-hidden">
            <ul className="max-h-[620px] divide-y divide-rule overflow-y-auto" role="listbox" aria-label="Engineering terms">
              {results.map((t) => {
                const isSelected = selected?.id === t.id
                return (
                  <li key={t.id}>
                    <button
                      type="button"
                      role="option"
                      aria-selected={isSelected}
                      onClick={() => selectTerm(t.id)}
                      className={`flex w-full items-start justify-between gap-2 px-4 py-3 text-left transition-colors hover:bg-raised ${
                        isSelected ? 'bg-accent/10' : ''
                      }`}
                    >
                      <div className="min-w-0">
                        <div className={`text-sm font-medium ${isSelected ? 'text-accent' : 'text-ink'}`}>{t.term}</div>
                        <div className="mt-0.5 line-clamp-2 text-xs text-muted">{t.simple}</div>
                        <div className="mt-1 text-[10px] uppercase tracking-wider text-faint">{categoryLabel(t.category)}</div>
                      </div>
                      <ChevronRight className={`mt-1 h-4 w-4 shrink-0 ${isSelected ? 'text-accent' : 'text-faint'}`} />
                    </button>
                  </li>
                )
              })}
            </ul>
          </Card>

          {/* Detail Panel */}
          {selected && (
            <Card className="p-6 space-y-5">
              <div className="flex flex-wrap items-start justify-between gap-3 border-b border-rule pb-4">
                <div>
                  <Badge className="bg-accent/15 text-accent">{categoryLabel(selected.category)}</Badge>
                  <h2 className="mt-2 text-xl font-semibold text-ink">{selected.term}</h2>
                </div>
                <Badge className="bg-raised text-muted">
                  {learningMode === 'beginner' ? 'Beginner mode' : 'Technical mode'}
                </Badge>
              </div>

              {learningMode === 'beginner' ? (
                <div className="space-y-4">
                  <div>
                    <h3 className="text-xs font-semibold uppercase tracking-wider text-faint">In Plain English</h3>
                    <p className="mt-1.5 text-sm leading-relaxed text-ink">{selected.simple}</p>
                  </div>
                  {selected.analogy && (
                    <div className="rounded-card border border-edge bg-ground p-4">
                      <h3 className="text-xs font-semibold uppercase tracking-wider text-accent">Everyday Analogy</h3>
                      <p className="mt-1 text-sm leading-relaxed text-muted">{selected.analogy}</p>
                    </div>
                  )}
                </div>
              ) : (
                <div className="space-y-4">
                  <div>
                    <h3 className="text-xs font-semibold uppercase tracking-wider text-faint">Technical Definition</h3>
                    <p className="mt-1.5 text-sm leading-relaxed text-ink">{selected.technical}</p>
                  </div>
                  {selected.formula && (
                    <div className="rounded-card border border-edge bg-well p-4">
                      <h3 className="text-xs font-semibold uppercase tracking-wider text-faint">Implemented Formula & Scale</h3>
                      <code className="num mt-1.5 block text-sm text-accent">{selected.formula}</code>
                    </div>
                  )}
                </div>
              )}

              <div>
                <h3 className="text-xs font-semibold uppercase tracking-wider text-faint">Practical Example</h3>
                <p className="mt-1.5 text-sm leading-relaxed text-muted">{selected.example}</p>
              </div>

              <div>
                <h3 className="text-xs font-semibold uppercase tracking-wider text-faint">Why It Matters</h3>
                <p className="mt-1.5 text-sm leading-relaxed text-muted">{selected.whyItMatters}</p>
              </div>

              {selected.related?.length > 0 && (
                <div className="border-t border-rule pt-4">
                  <h3 className="mb-2 text-xs font-semibold uppercase tracking-wider text-faint">Related Concepts</h3>
                  <div className="flex flex-wrap gap-2">
                    {selected.related.map((relId) => {
                      const rel = getTerm(relId)
                      if (!rel) return null
                      return (
                        <button
                          key={relId}
                          type="button"
                          onClick={() => selectTerm(relId)}
                          className="rounded-full border border-edge bg-ground px-3 py-1 text-xs font-medium text-muted transition-colors hover:border-accent hover:text-ink"
                        >
                          {rel.term}
                        </button>
                      )
                    })}
                  </div>
                </div>
              )}
            </Card>
          )}
        </div>
      )}
    </>
  )
}
