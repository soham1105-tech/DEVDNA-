import { motion } from 'framer-motion'
import { Loader2 } from 'lucide-react'

const VARIANTS = {
  primary: 'bg-ink text-ground hover:opacity-90',
  secondary: 'border border-control bg-transparent text-ink hover:border-faint',
  ghost: 'text-muted hover:bg-raised hover:text-ink',
  danger: 'border border-negative/40 bg-negative-bg text-negative-ink hover:border-negative',
  cyan: 'bg-ink text-ground hover:opacity-90', // legacy name kept so existing callers map to the primary
}
const SIZES = { sm: 'h-7 px-3 text-xs gap-1.5', md: 'h-9 px-4 text-sm gap-2', lg: 'h-11 px-5 text-sm gap-2' }

export function Button({ variant = 'secondary', size = 'md', icon: Icon, loading, disabled, className = '', children, ...props }) {
  return (
    <motion.button
      whileTap={disabled || loading ? undefined : { scale: 0.97 }}
      disabled={disabled || loading}
      className={`inline-flex items-center justify-center rounded-control font-medium transition-colors focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent disabled:cursor-not-allowed disabled:opacity-50 ${VARIANTS[variant]} ${SIZES[size]} ${className}`}
      {...props}
    >
      {loading ? <Loader2 className="h-4 w-4 animate-spin" /> : Icon ? <Icon className="h-4 w-4" /> : null}
      {children}
    </motion.button>
  )
}

export function IconButton({ icon: Icon, label, className = '', ...props }) {
  return (
    <motion.button
      whileTap={{ scale: 0.92 }}
      aria-label={label}
      title={label}
      className={`inline-flex h-8 w-8 items-center justify-center rounded-control text-muted transition-colors hover:bg-raised hover:text-ink focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent ${className}`}
      {...props}
    >
      <Icon className="h-4 w-4" />
    </motion.button>
  )
}
