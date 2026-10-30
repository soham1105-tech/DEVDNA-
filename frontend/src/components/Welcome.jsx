import { motion } from 'framer-motion'
import { ArrowRight, FlaskConical, GitCompareArrows, Radar, ScanSearch, UploadCloud, Workflow } from 'lucide-react'
import { useNavigate } from 'react-router-dom'
import { item, stagger } from '../animations/variants'
import { useAnalysisFlow } from '../hooks/useAnalysisFlow'
import { useStatus } from '../hooks/useStatus'
import AnalysisProgress from './AnalysisProgress'
import { ErrorState, InlineNotice } from './ui'

export const WORKFLOW = [
  { key: 'discover', label: 'Discover', icon: ScanSearch, text: 'Static AST analysis of real source code' },
  { key: 'visualize', label: 'Visualize', icon: Workflow, text: 'Interactive, evidence-backed graph' },
  { key: 'predict', label: 'Predict', icon: Radar, text: 'Deterministic blast radius + Gemma 4' },
  { key: 'experiment', label: 'Experiment', icon: FlaskConical, text: 'Approved fault injection in a sandbox' },
  { key: 'compare', label: 'Compare', icon: GitCompareArrows, text: 'Prediction vs. observed reality' },
]

/** First screen: two clear actions, analyse a repository or explore the demo. */
export default function Welcome({ onAnalyse }) {
  const flow = useAnalysisFlow()
  const status = useStatus()
  const navigate = useNavigate()

  const demo = async () => {
    const res = await flow.start('sample')
    if (res) navigate('/')
  }

  return (
    <motion.div variants={stagger} initial="initial" animate="animate" className="mx-auto max-w-5xl py-6 lg:py-12">
      <motion.div variants={item}>
        <h1 className="max-w-3xl text-balance text-3xl font-semibold tracking-tight text-ink sm:text-4xl">
          The digital twin for your software system
        </h1>
        <p className="mt-4 max-w-2xl text-balance text-muted">
          DevDNA maps your architecture from source code, predicts what breaks when a dependency fails, then proves or disproves the prediction with a controlled experiment. Powered by Gemma 4, open-source AI.
        </p>
      </motion.div>

      {status.backend === 'offline' && (
        <motion.div variants={item} className="mt-6 max-w-xl">
          <InlineNotice tone="warn">The backend is not reachable. Start it with <code className="num">uvicorn app.main:app --port 8000</code> in the backend folder.</InlineNotice>
        </motion.div>
      )}

      <motion.div variants={item} className="mt-10 grid items-start gap-4 md:grid-cols-2">
        <motion.button
          whileHover={{ y: -3 }}
          whileTap={{ scale: 0.99 }}
          onClick={onAnalyse}
          className="group rounded-card border border-edge bg-surface p-6 text-left transition-colors hover:border-control"
        >
          <div className="flex items-center gap-2.5">
            <UploadCloud className="h-5 w-5 text-faint" />
            <h2 className="text-lg font-semibold text-ink">Analyse a Repository</h2>
          </div>
          <p className="mt-2 text-sm text-muted">Upload a ZIP of a Python project or point to an allowed local folder. Secrets and generated files are skipped.</p>
          <div className="mt-4 flex items-center gap-1 text-sm text-accent">
            Choose source <ArrowRight className="h-4 w-4 transition-transform group-hover:translate-x-1" />
          </div>
        </motion.button>
        <motion.button
          whileHover={{ y: -3 }}
          whileTap={{ scale: 0.99 }}
          onClick={demo}
          disabled={flow.busy}
          className="group rounded-card border border-accent/40 bg-surface p-6 text-left transition-colors hover:border-accent disabled:cursor-wait"
        >
          <div className="flex items-center gap-2.5">
            <ScanSearch className="h-5 w-5 text-accent" />
            <h2 className="text-lg font-semibold text-ink">Explore Demo Project</h2>
          </div>
          <p className="mt-2 text-sm text-muted">ShopLite: a FastAPI shop with SQLite, an external shipping API, a background worker and a real pytest suite. Works offline, no API key needed.</p>
          {flow.busy || flow.run ? (
            <div className="mt-4" onClick={(e) => e.stopPropagation()}>
              <AnalysisProgress run={flow.run} />
            </div>
          ) : (
            <div className="mt-4 flex items-center gap-1 text-sm text-accent">
              Load and analyse <ArrowRight className="h-4 w-4 transition-transform group-hover:translate-x-1" />
            </div>
          )}
        </motion.button>
      </motion.div>
      {flow.error && (
        <div className="mt-4">
          <ErrorState title="Could not load the demo" error={flow.error} onRetry={demo} />
        </div>
      )}

      <motion.ol variants={item} className="mt-12 grid items-start gap-2 sm:grid-cols-5">
        {WORKFLOW.map((w) => (
          <li key={w.key} className="relative rounded-card border border-edge bg-surface p-3">
            <div className="flex items-center gap-2">
              <w.icon className="h-4 w-4 text-accent" />
              <span className="text-sm font-medium text-ink">{w.label}</span>
            </div>
            <p className="mt-1 text-xs text-faint">{w.text}</p>
          </li>
        ))}
      </motion.ol>
    </motion.div>
  )
}
