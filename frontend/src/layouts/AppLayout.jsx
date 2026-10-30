import { AnimatePresence, motion } from 'framer-motion'
import {
  Activity, Beaker, ChevronDown, FileText, FlaskConical, Menu, MessageSquareText, Moon, Plus, Radar, Settings, ShieldAlert, Sun, Workflow, X,
} from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import { NavLink, useLocation, useOutlet } from 'react-router-dom'
import { page } from '../animations/variants'
import ChatDrawer from '../components/ChatDrawer'
import NewProjectModal from '../components/NewProjectModal'
import { IconButton } from '../components/ui'
import { useProject } from '../hooks/useProject'
import { useSettings } from '../hooks/useSettings'
import { useStatus } from '../hooks/useStatus'

export const NAV_ITEMS = [
  { to: '/', label: 'Overview', icon: Activity },
  { to: '/architecture', label: 'Architecture', icon: Workflow },
  { to: '/prediction', label: 'Failure Prediction', icon: Radar },
  { to: '/lab', label: 'What-If Lab', icon: Beaker },
  { to: '/findings', label: 'Findings', icon: ShieldAlert },
  { to: '/experiments', label: 'Experiments', icon: FlaskConical },
  { to: '/reports', label: 'Reports', icon: FileText },
  { to: '/settings', label: 'Settings', icon: Settings },
]

export function ThemeToggle({ className = '' }) {
  const { settings, toggleTheme } = useSettings()
  const isBright = settings.theme === 'bright'

  return (
    <button
      type="button"
      onClick={toggleTheme}
      aria-label={isBright ? 'Switch to Dark mode' : 'Switch to Bright mode'}
      title={isBright ? 'Switch to Dark mode (Ctrl+Shift+D)' : 'Switch to Bright mode (Ctrl+Shift+D)'}
      className={`flex h-8 w-8 shrink-0 items-center justify-center rounded-control border border-control text-muted transition-colors hover:border-faint hover:text-ink ${className}`}
    >
      <motion.div
        key={isBright ? 'sun' : 'moon'}
        initial={{ rotate: -45, scale: 0.5, opacity: 0 }}
        animate={{ rotate: 0, scale: 1, opacity: 1 }}
        exit={{ rotate: 45, scale: 0.5, opacity: 0 }}
        transition={{ duration: 0.2 }}
      >
        {isBright ? <Sun className="h-4 w-4" /> : <Moon className="h-4 w-4" />}
      </motion.div>
    </button>
  )
}

function Logo() {
  return (
    <div className="flex items-center gap-2.5">
      <svg viewBox="0 0 32 32" className="h-7 w-7" aria-hidden>
        <path d="M11 4c0 8 10 8 10 12s-10 4-10 12M21 4c0 8-10 8-10 12s10 4 10 12" fill="none" stroke="var(--color-accent)" strokeWidth="2.2" strokeLinecap="round" />
        <path d="M13 10h6M13 22h6" stroke="var(--color-ink)" strokeWidth="1.6" strokeLinecap="round" opacity="0.7" />
      </svg>
      <div className="text-sm font-semibold tracking-tight text-ink">DevDNA</div>
    </div>
  )
}

function StatusIndicator() {
  const { backend, ai } = useStatus()
  const backendDot = backend === 'online' ? 'bg-positive' : backend === 'offline' ? 'bg-negative' : 'bg-warning'
  const aiOk = ai?.configured && ai?.last_check?.available !== false
  const aiDot = !ai ? 'bg-control' : aiOk ? 'bg-accent' : ai.configured ? 'bg-warning' : 'bg-control'
  return (
    <NavLink to="/settings" className="block rounded-card border border-edge bg-surface p-3 text-xs transition-colors hover:border-control">
      <div className="flex items-center justify-between">
        <span className="text-muted">Backend</span>
        <span className="flex items-center gap-1.5 text-ink">
          <span className={`h-1.5 w-1.5 rounded-full ${backendDot}`} />
          {backend}
        </span>
      </div>
      <div className="mt-1.5 flex items-center justify-between gap-2">
        <span className="text-muted">Gemma</span>
        <span className="flex min-w-0 items-center gap-1.5 text-ink">
          <span className={`h-1.5 w-1.5 shrink-0 rounded-full ${aiDot}`} />
          <span className="truncate">{!ai ? '—' : ai.configured ? (ai.last_check?.available === false ? 'unavailable' : 'configured') : 'not configured'}</span>
        </span>
      </div>
      {ai?.model && <div className="num mt-1 truncate text-[11px] text-faint">{ai.model}</div>}
      {ai?.last_request && (
        <div className="mt-0.5 truncate text-[11px] text-faint">last request: {ai.last_request.status}</div>
      )}
    </NavLink>
  )
}

function ProjectSelector({ onNew }) {
  const { projects, current, setCurrentId } = useProject()
  const [open, setOpen] = useState(false)
  const ref = useRef(null)
  useEffect(() => {
    const h = (e) => ref.current && !ref.current.contains(e.target) && setOpen(false)
    document.addEventListener('mousedown', h)
    return () => document.removeEventListener('mousedown', h)
  }, [])
  return (
    <div className="relative" ref={ref}>
      <button
        onClick={() => setOpen((o) => !o)}
        aria-haspopup="listbox"
        aria-expanded={open}
        className="flex w-full items-center justify-between gap-2 rounded-card border border-edge bg-surface px-3 py-2 text-left text-sm transition-colors hover:border-control"
      >
        <div className="min-w-0">
          <div className="text-xs text-faint">Project</div>
          <div className="truncate text-ink">{current?.name || 'No project yet'}</div>
        </div>
        <motion.span animate={{ rotate: open ? 180 : 0 }}>
          <ChevronDown className="h-4 w-4 text-faint" />
        </motion.span>
      </button>
      <AnimatePresence>
        {open && (
          <motion.div
            initial={{ opacity: 0, y: -4 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: -4 }}
            transition={{ duration: 0.14 }}
            className="absolute left-0 right-0 z-30 mt-1 overflow-hidden rounded-card border border-edge bg-surface shadow-[0_12px_28px_rgba(0,0,0,0.2)]"
            role="listbox"
          >
            <div className="max-h-64 overflow-y-auto py-1">
              {projects.map((p) => (
                <button
                  key={p.id}
                  role="option"
                  aria-selected={p.id === current?.id}
                  onClick={() => {
                    setCurrentId(p.id)
                    setOpen(false)
                  }}
                  className={`flex w-full items-center justify-between px-3 py-2 text-left text-sm hover:bg-raised ${p.id === current?.id ? 'text-ink' : 'text-muted'}`}
                >
                  <span className="truncate">{p.name}</span>
                  <span className="ml-2 shrink-0 text-[11px] text-faint">{p.source_type}</span>
                </button>
              ))}
            </div>
            <button
              onClick={() => {
                setOpen(false)
                onNew()
              }}
              className="flex w-full items-center justify-between border-t border-rule px-3 py-2 text-sm text-accent hover:bg-raised"
            >
              <span className="flex items-center gap-2">
                <Plus className="h-4 w-4" /> New project
              </span>
              <kbd className="num rounded border border-control px-1 py-0.5 text-[11px] text-faint">⌘K</kbd>
            </button>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  )
}

function NavList({ onNavigate }) {
  return (
    <nav className="space-y-0.5" aria-label="Main">
      {NAV_ITEMS.map(({ to, label, icon: Icon }) => (
        <NavLink key={to} to={to} end={to === '/'} onClick={onNavigate} className="relative block">
          {({ isActive }) => (
            <div className={`relative flex items-center gap-2.5 rounded-card py-2 pl-4 pr-3 text-sm transition-colors ${isActive ? 'text-ink' : 'text-muted hover:text-ink'}`}>
              {/* The selection mark: a 1px accent line on the sidebar's edge, as wide as the label. */}
              {isActive && <motion.span layoutId="nav-active" className="absolute left-0 top-1.5 bottom-1.5 w-px bg-accent" transition={{ type: 'spring', stiffness: 500, damping: 40 }} />}
              <Icon className={`relative h-4 w-4 ${isActive ? 'text-accent' : 'text-faint'}`} />
              <span className="relative">{label}</span>
            </div>
          )}
        </NavLink>
      ))}
    </nav>
  )
}

export default function AppLayout() {
  const [newOpen, setNewOpen] = useState(false)
  const [chatOpen, setChatOpen] = useState(false)
  const [mobileNav, setMobileNav] = useState(false)
  const { current, ready } = useProject()
  const { toggleTheme } = useSettings()
  const location = useLocation()
  const outlet = useOutlet({ openNewProject: () => setNewOpen(true), openChat: () => setChatOpen(true) })

  useEffect(() => setMobileNav(false), [location.pathname])

  // Desktop sidebar row: logo + theme toggle. Mobile sheet swaps the toggle (already
  // in the top bar) for a visible close button, so the two never overlap.
  const makeSidebar = (mobile) => (
    <div className="flex h-full flex-col gap-5 p-4">
      <div className="flex items-center justify-between">
        <Logo />
        {mobile ? (
          <IconButton icon={X} label="Close navigation" onClick={() => setMobileNav(false)} />
        ) : (
          <ThemeToggle />
        )}
      </div>
      <ProjectSelector onNew={() => setNewOpen(true)} />
      <NavList onNavigate={() => setMobileNav(false)} />
      <div className="mt-auto space-y-3">
        <button
          onClick={() => setChatOpen(true)}
          disabled={!ready}
          className="flex w-full items-center justify-between rounded-card border border-edge bg-surface px-3 py-2 text-sm text-ink transition-colors hover:border-control disabled:cursor-not-allowed disabled:opacity-40"
          title={ready ? 'Ask questions about the analysed repository (Ctrl/Cmd+J)' : 'Analyse a project first'}
        >
          <span className="flex items-center gap-2">
            <MessageSquareText className="h-4 w-4 text-faint" /> Ask DevDNA
          </span>
          <kbd className="num hidden rounded border border-control px-1.5 py-0.5 text-[11px] text-faint sm:inline-block">⌘J</kbd>
        </button>
        <StatusIndicator />
      </div>
    </div>
  )
  const sidebar = makeSidebar(false)

  useEffect(() => {
    const handleKeyDown = (e) => {
      const isInput = ['INPUT', 'TEXTAREA', 'SELECT'].includes(document.activeElement?.tagName) || document.activeElement?.isContentEditable
      if (e.key === 'Escape') {
        setNewOpen(false)
        setChatOpen(false)
        setMobileNav(false)
      } else if ((e.ctrlKey || e.metaKey) && e.shiftKey && e.key.toLowerCase() === 'd') {
        e.preventDefault()
        toggleTheme()
      } else if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'k') {
        e.preventDefault()
        setNewOpen(true)
      } else if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'j') {
        e.preventDefault()
        if (ready) setChatOpen((o) => !o)
      } else if (!isInput && e.key === '/' && !e.ctrlKey && !e.metaKey) {
        e.preventDefault()
        setNewOpen(true)
      }
    }
    window.addEventListener('keydown', handleKeyDown)
    return () => window.removeEventListener('keydown', handleKeyDown)
  }, [ready, toggleTheme])

  return (
    <div className="relative flex h-full">
      <aside className="relative z-10 hidden w-60 shrink-0 border-r border-edge bg-ground lg:block">{sidebar}</aside>

      {/* Mobile top bar + slide-in navigation */}
      <div className="fixed inset-x-0 top-0 z-30 flex h-12 items-center justify-between border-b border-edge bg-ground px-4 lg:hidden">
        <Logo />
        <div className="flex items-center gap-1.5">
          <ThemeToggle />
          <button aria-label="Ask DevDNA" disabled={!ready} onClick={() => setChatOpen(true)} className="rounded-control p-2 text-muted disabled:opacity-40">
            <MessageSquareText className="h-5 w-5" />
          </button>
          <button aria-label="Open navigation" onClick={() => setMobileNav(true)} className="rounded-control p-2 text-ink">
            <Menu className="h-5 w-5" />
          </button>
        </div>
      </div>
      <AnimatePresence>
        {mobileNav && (
          <motion.div className="fixed inset-0 z-40 lg:hidden" initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}>
            <div className="absolute inset-0 bg-black/60" onClick={() => setMobileNav(false)} />
            <motion.aside
              initial={{ x: '-100%' }}
              animate={{ x: 0 }}
              exit={{ x: '-100%' }}
              transition={{ type: 'spring', stiffness: 400, damping: 40 }}
              className="absolute left-0 top-0 h-full w-72 border-r border-edge bg-ground"
            >
              {makeSidebar(true)}
            </motion.aside>
          </motion.div>
        )}
      </AnimatePresence>

      <main className="relative z-10 min-w-0 flex-1 overflow-x-hidden overflow-y-auto pt-12 lg:pt-0">
        <div className="mx-auto w-full max-w-[1400px] px-4 py-6 sm:px-6 lg:px-8 lg:py-8">
          {/* Keyed entrance animation per page (no exit wait, so navigation can never stall) */}
          <motion.div key={location.pathname} variants={page} initial="initial" animate="animate">
            {outlet}
          </motion.div>
        </div>
      </main>

      <NewProjectModal open={newOpen} onClose={() => setNewOpen(false)} />
      {current && <ChatDrawer open={chatOpen} onClose={() => setChatOpen(false)} project={current} />}
    </div>
  )
}
