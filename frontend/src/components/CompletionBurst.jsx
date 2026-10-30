import { motion, useReducedMotion } from 'framer-motion'
import { CheckCircle2, CircleHelp, XCircle } from 'lucide-react'

const TONES = {
  prediction_confirmed: { icon: CheckCircle2, cls: 'text-positive', ring: 'border-positive/40 bg-positive/10', text: 'Prediction confirmed' },
  partially_confirmed: { icon: CircleHelp, cls: 'text-warning', ring: 'border-warning/40 bg-warning/10', text: 'Partially confirmed' },
  prediction_contradicted: { icon: XCircle, cls: 'text-negative', ring: 'border-negative/40 bg-negative/10', text: 'Prediction contradicted' },
  inconclusive: { icon: CircleHelp, cls: 'text-muted', ring: 'border-control bg-raised', text: 'Inconclusive' },
}

/** A short, professional completion moment when an experiment finishes. Plays once. */
export default function CompletionBurst({ status }) {
  const reduce = useReducedMotion()
  const t = TONES[status] || TONES.inconclusive
  const Icon = t.icon
  return (
    <div className="relative flex flex-col items-center py-6">
      {!reduce &&
        [0, 1, 2].map((i) => (
          <motion.span
            key={i}
            className={`absolute top-6 h-16 w-16 rounded-full border ${t.ring}`}
            initial={{ scale: 0.6, opacity: 0.7 }}
            animate={{ scale: 2.6, opacity: 0 }}
            transition={{ duration: 1.3, delay: i * 0.18, ease: 'easeOut' }}
          />
        ))}
      <motion.div
        initial={{ scale: 0.4, opacity: 0 }}
        animate={{ scale: 1, opacity: 1 }}
        transition={{ type: 'spring', stiffness: 380, damping: 18 }}
        className={`relative flex h-16 w-16 items-center justify-center rounded-full border ${t.ring}`}
      >
        <Icon className={`h-8 w-8 ${t.cls}`} />
      </motion.div>
      <motion.div initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: 0.2 }} className={`mt-3 text-base font-semibold ${t.cls}`}>
        {t.text}
      </motion.div>
    </div>
  )
}
