import { AnimatePresence, motion } from 'framer-motion'
import { Info, RefreshCw, Sparkles } from 'lucide-react'
import { useState } from 'react'
import { Link } from 'react-router-dom'
import { useGemma } from '../hooks/useGemma'
import { useStatus } from '../hooks/useStatus'
import { timeAgo } from '../utils/format'
import { Badge, Button, Card, CardHeader, ErrorState, InlineNotice, Skeleton } from './ui'

/**
 * Generic "ask Gemma" card. `fetcher(body)` calls the backend endpoint, `render(result)` draws the validated JSON.
 * `initial` is a previously stored result ({ result, meta }).
 */
export default function AIPanel({ title, subtitle, purpose, fetcher, render, initial, actionLabel = 'Generate with Gemma', onResult }) {
  const { runAI } = useGemma()
  const status = useStatus()
  const [state, setState] = useState({ loading: false, error: null, data: initial || null })
  const configured = status?.ai?.configured

  const run = async () => {
    setState((s) => ({ ...s, loading: true, error: null }))
    try {
      const data = await runAI(fetcher, purpose)
      setState({ loading: false, error: null, data })
      onResult?.(data)
    } catch (error) {
      setState((s) => ({ ...s, loading: false, error: error.code === 'cancelled' ? null : error }))
    }
  }

  const data = state.data
  return (
    <Card className="overflow-hidden">
      <CardHeader
        icon={Sparkles}
        title={title}
        subtitle={subtitle}
        action={
          <Button size="sm" variant={data ? 'ghost' : 'primary'} icon={data ? RefreshCw : Sparkles} loading={state.loading} onClick={run} disabled={configured === false}>
            {data ? 'Regenerate' : actionLabel}
          </Button>
        }
      />
      <div className="p-4">
        {configured === false && !data && (
          <InlineNotice tone="violet" icon={Info}>
            Gemma is not configured, so AI explanations are off. Static analysis, graphs, predictions and experiments still work.{' '}
            <Link to="/settings" className="font-medium text-violet-200 underline underline-offset-2">How to enable Gemma</Link>
          </InlineNotice>
        )}
        {state.error && <ErrorState title="Gemma request failed" error={state.error} onRetry={run} />}
        {state.loading && (
          <div className="space-y-2">
            <div className="flex items-center gap-2 text-xs text-violet-200">
              <motion.span animate={{ opacity: [0.4, 1, 0.4] }} transition={{ repeat: Infinity, duration: 1.4 }}>
                <Sparkles className="h-3.5 w-3.5" />
              </motion.span>
              Asking {status?.ai?.model}… Gemma reasons before answering, so this can take 1–2 minutes.
            </div>
            <Skeleton className="h-4 w-11/12" />
            <Skeleton className="h-4 w-9/12" />
            <Skeleton className="h-4 w-10/12" />
          </div>
        )}
        <AnimatePresence initial={false}>
          {data && !state.loading && (
            <motion.div key={data.meta?.interaction_id || data.meta?.generated_at} initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0 }}>
              {render(data.result)}
              <div className="mt-4 flex flex-wrap items-center gap-2 border-t border-white/[0.05] pt-3 text-[11px] text-ink-400">
                <Badge className="border-violet-400/20 bg-violet-400/10 font-mono text-violet-200">{data.meta?.model}</Badge>
                <span>generated {timeAgo(data.meta?.generated_at)}</span>
                {data.meta?.latency_ms ? <span>· {(data.meta.latency_ms / 1000).toFixed(1)} s</span> : null}
                {data.meta?.ungrounded_refs?.length > 0 && (
                  <span className="text-amber-300">· removed {data.meta.ungrounded_refs.length} citation(s) not in the evidence</span>
                )}
                <span>· AI-generated explanation of deterministic results</span>
              </div>
            </motion.div>
          )}
        </AnimatePresence>
        {!data && !state.loading && !state.error && configured !== false && (
          <p className="text-sm text-ink-300">Nothing generated yet. Architecture, prediction, and chat requests offer a preview of the redacted evidence before sending. Multimodal audits and dynamic-scenario synthesis use an explicit checkbox confirmation instead.</p>
        )}
      </div>
    </Card>
  )
}

export function List({ title, items, tone = 'text-ink-200' }) {
  if (!items?.length) return null
  return (
    <div className="mt-3">
      <div className="mb-1 text-xs font-medium uppercase tracking-wider text-ink-400">{title}</div>
      <ul className={`space-y-1 text-sm ${tone}`}>
        {items.map((x, i) => (
          <li key={i} className="flex gap-2">
            <span className="mt-2 h-1 w-1 shrink-0 rounded-full bg-ink-400" />
            <span>{typeof x === 'string' ? x : <><span className="font-medium text-ink-100">{x.title || x.name || x.test}:</span> {x.detail || x.rationale || x.explanation}</>}</span>
          </li>
        ))}
      </ul>
    </div>
  )
}

export function Refs({ refs }) {
  if (!refs?.length) return null
  return (
    <div className="mt-3 flex flex-wrap gap-1">
      <span className="text-xs text-ink-400">Evidence cited:</span>
      {refs.map((r) => (
        <Badge key={r} className="border-violet-400/20 bg-violet-400/10 font-mono text-violet-200">{r}</Badge>
      ))}
    </div>
  )
}
