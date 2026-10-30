import { AnimatePresence, motion } from 'framer-motion'
import { ChevronRight, ShieldCheck } from 'lucide-react'
import { useEffect, useState } from 'react'
import { collapse } from '../animations/variants'
import { Badge, Button, Modal } from './ui'

/** Shows exactly which redacted evidence will be sent to Gemma, and asks for explicit consent. */
export default function ConsentModal({ open, preview, purpose, sessionOption, onCancel, onConfirm }) {
  const [agreed, setAgreed] = useState(false)
  const [remember, setRemember] = useState(false)
  const [expanded, setExpanded] = useState(null)

  useEffect(() => {
    if (open) {
      setAgreed(false)
      setRemember(false)
      setExpanded(null)
    }
  }, [open])

  const items = preview?.evidence?.items || []
  return (
    <Modal
      open={open}
      onClose={onCancel}
      width="max-w-2xl"
      title="Send evidence to Gemma?"
      subtitle={purpose ? `${purpose} · model ${preview?.model}` : `Model ${preview?.model}`}
      footer={
        <>
          <Button variant="ghost" onClick={onCancel}>
            Cancel
          </Button>
          <Button variant="primary" icon={ShieldCheck} disabled={!agreed} onClick={() => onConfirm(remember)}>
            Send to Gemma
          </Button>
        </>
      }
    >
      <p className="text-sm text-muted">
        DevDNA will send the {items.length} evidence items below ({preview?.evidence?.total_chars?.toLocaleString()} characters) to the Google
        Gemini API to run <span className="num text-ink">{preview?.model}</span>. Secrets are redacted and nothing else from your repository is sent.
      </p>
      <ul className="mt-4 space-y-1.5">
        {items.map((it) => (
          <li key={it.ref} className="rounded-card border border-edge bg-ground">
            <button
              className="flex w-full items-center gap-2 px-3 py-2 text-left text-sm"
              onClick={() => setExpanded(expanded === it.ref ? null : it.ref)}
              aria-expanded={expanded === it.ref}
            >
              <motion.span animate={{ rotate: expanded === it.ref ? 90 : 0 }}>
                <ChevronRight className="h-3.5 w-3.5 text-faint" />
              </motion.span>
              <Badge className="bg-accent/10 text-accent"><span className="num">{it.ref}</span></Badge>
              <span className="text-xs text-faint">{it.kind}</span>
              <span className="truncate text-muted">{it.title}</span>
            </button>
            <AnimatePresence initial={false}>
              {expanded === it.ref && (
                <motion.pre variants={collapse} initial="initial" animate="animate" exit="exit" className="num overflow-hidden whitespace-pre-wrap break-words border-t border-rule px-3 py-2 text-[11px] leading-relaxed text-muted">
                  {it.content}
                </motion.pre>
              )}
            </AnimatePresence>
          </li>
        ))}
      </ul>
      <label className="mt-4 flex cursor-pointer items-start gap-2.5 text-sm text-muted">
        <input type="checkbox" className="mt-0.5 h-4 w-4 accent-[var(--color-accent)]" checked={agreed} onChange={(e) => setAgreed(e.target.checked)} />
        I agree to send this evidence to the Gemini API.
      </label>
      {sessionOption && (
        <label className="mt-2 flex cursor-pointer items-start gap-2.5 text-sm text-muted">
          <input type="checkbox" className="mt-0.5 h-4 w-4 accent-[var(--color-accent)]" checked={remember} onChange={(e) => setRemember(e.target.checked)} />
          Don&apos;t ask again until I close this tab.
        </label>
      )}
    </Modal>
  )
}
