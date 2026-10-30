import { motion } from 'framer-motion'

// Cards animate themselves on mount instead of inheriting variant labels from parents,
// so a card that mounts later (e.g. after an AI response) can never get stuck hidden.
export function Card({ className = '', interactive = false, children, ...props }) {
  return (
    <motion.div
      initial={{ opacity: 0, y: 8 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.28, ease: [0.22, 1, 0.36, 1] }}
      whileHover={interactive ? { y: -2, borderColor: 'var(--color-control)' } : undefined}
      whileTap={interactive ? { scale: 0.99 } : undefined}
      className={`min-w-0 rounded-card border border-edge bg-surface ${interactive ? 'cursor-pointer' : ''} ${className}`}
      {...props}
    >
      {children}
    </motion.div>
  )
}

export function CardHeader({ title, subtitle, icon: Icon, action, className = '' }) {
  return (
    <div className={`flex items-start justify-between gap-3 border-b border-rule px-4 py-3 ${className}`}>
      <div className="flex min-w-0 items-center gap-2.5">
        {Icon && <Icon className="h-4 w-4 shrink-0 text-faint" />}
        <div className="min-w-0">
          <h3 className="truncate text-[15px] font-semibold text-ink">{title}</h3>
          {subtitle && <p className="truncate text-xs text-muted">{subtitle}</p>}
        </div>
      </div>
      {action}
    </div>
  )
}

/** One figure in a divided KPI strip: a label above a mono value, no tile of its own. */
export function Stat({ label, value, hint, icon: Icon, accent, className = '' }) {
  return (
    <div className={`min-w-0 px-4 py-3.5 ${className}`}>
      <div className="flex items-center gap-1.5 text-xs text-muted">
        {Icon && <Icon className="h-3.5 w-3.5 text-faint" />}
        <span className="truncate">{label}</span>
      </div>
      <div className="num mt-1 text-2xl leading-7 font-medium text-ink">{value}</div>
      {hint && <div className="mt-1 truncate text-xs text-faint">{hint}</div>}
    </div>
  )
}

/** A row of Stat cells separated by 1px rules inside one container. */
export function StatStrip({ children, className = '' }) {
  return (
    <div className={`grid grid-cols-2 divide-rule rounded-card border border-edge bg-surface sm:grid-cols-3 lg:grid-cols-6 lg:divide-x ${className}`}>
      {children}
    </div>
  )
}

export function PageHeader({ title, subtitle, actions, eyebrow }) {
  return (
    <div className="mb-6 flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
      <div className="min-w-0">
        {eyebrow && <div className="mb-0.5 text-xs text-faint">{eyebrow}</div>}
        <h1 className="text-2xl font-semibold tracking-tight text-ink">{title}</h1>
        {subtitle && <p className="mt-1 max-w-2xl text-sm text-muted">{subtitle}</p>}
      </div>
      {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
    </div>
  )
}
