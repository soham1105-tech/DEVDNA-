import { motion } from 'framer-motion'
import { AlertTriangle, Loader2, RefreshCw } from 'lucide-react'
import { Button } from './Button'

/** Loading keeps the final layout: a flat bar at the value's real size, no shimmer. */
export function Skeleton({ className = '' }) {
  return <div className={`animate-pulse rounded-md bg-raised ${className}`} />
}

export function SkeletonCards({ count = 4, className = 'h-24' }) {
  return (
    <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
      {Array.from({ length: count }).map((_, i) => (
        <Skeleton key={i} className={className} />
      ))}
    </div>
  )
}

export function Spinner({ label }) {
  return (
    <div className="flex items-center gap-2 text-sm text-muted">
      <Loader2 className="h-4 w-4 animate-spin text-accent" />
      {label}
    </div>
  )
}

export function EmptyState({ icon: Icon, title, description, action }) {
  return (
    <motion.div initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }} className="flex flex-col items-center justify-center rounded-card border border-dashed border-control bg-surface px-6 py-12 text-center">
      {Icon && <Icon className="mb-3 h-5 w-5 text-faint" />}
      <h3 className="text-sm font-semibold text-ink">{title}</h3>
      {description && <p className="mt-1 max-w-md text-sm text-muted">{description}</p>}
      {action && <div className="mt-4">{action}</div>}
    </motion.div>
  )
}

export function ErrorState({ error, onRetry, title = 'Something went wrong' }) {
  return (
    <motion.div initial={{ opacity: 0 }} animate={{ opacity: 1 }} role="alert" className="flex items-start gap-3 rounded-card border border-negative/25 bg-negative-bg p-4">
      <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-negative" />
      <div className="min-w-0 flex-1">
        <div className="text-sm font-medium text-ink">{title}</div>
        <div className="mt-0.5 text-sm text-muted">{error?.message || String(error)}</div>
        {error?.fields?.length > 0 && (
          <ul className="mt-1 text-xs text-muted">
            {error.fields.map((f) => (
              <li key={f.field}>{f.field}: {f.message}</li>
            ))}
          </ul>
        )}
      </div>
      {onRetry && (
        <Button size="sm" variant="ghost" icon={RefreshCw} onClick={() => onRetry()}>
          Retry
        </Button>
      )}
    </motion.div>
  )
}

export function InlineNotice({ tone = 'info', icon: Icon, children }) {
  const cls = {
    info: 'border-accent/30 bg-accent/10 text-ink',
    warn: 'border-warning/30 bg-warning-bg text-ink',
    violet: 'border-accent/30 bg-accent/10 text-ink', // legacy tone name kept for callers
  }[tone]
  return (
    <div className={`flex items-start gap-2.5 rounded-card border px-3 py-2.5 text-sm ${cls}`}>
      {Icon && <Icon className="mt-0.5 h-4 w-4 shrink-0 text-muted" />}
      <div className="min-w-0">{children}</div>
    </div>
  )
}
