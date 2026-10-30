import { motion } from 'framer-motion'
import { Beaker, ChevronRight, FlaskConical } from 'lucide-react'
import { Link } from 'react-router-dom'
import { item, stagger } from '../animations/variants'
import ProjectGate from '../components/ProjectGate'
import { Button, Card, CardHeader, EmptyState, ErrorState, PageHeader, Skeleton, StatusBadge } from '../components/ui'
import { useAsync } from '../hooks/useAsync'
import { api } from '../services/api'
import { pct, timeAgo } from '../utils/format'
import { shortId } from '../utils/graph'

export default function Experiments() {
  return <ProjectGate>{(project) => <ExperimentsPage project={project} />}</ProjectGate>
}

function ExperimentsPage({ project }) {
  const exps = useAsync(() => api.experiments(project.id), [project.id])
  const preds = useAsync(() => api.predictions(project.id), [project.id])
  const tested = new Set((exps.data || []).map((e) => e.prediction_id))
  const untested = (preds.data || []).filter((p) => !tested.has(p.id))

  return (
    <>
      <PageHeader title="Experiment history" subtitle="Every executed experiment with its verdict, plus predictions that have not been validated yet."
        actions={<Link to="/lab"><Button variant="primary" icon={Beaker}>New experiment</Button></Link>} />
      {(exps.loading && !exps.data) ? <Skeleton className="h-40" /> : exps.error ? <ErrorState error={exps.error} onRetry={exps.reload} /> : (
        <motion.div variants={stagger} initial="initial" animate="animate" className="space-y-4">
          {exps.data.length === 0 && untested.length === 0 && (
            <EmptyState icon={FlaskConical} title="No experiments yet" description="Use the What-If Lab to predict a failure and validate it against the real test suite." action={<Link to="/lab"><Button variant="primary">Open What-If Lab</Button></Link>} />
          )}
          {exps.data.length > 0 && (
            <Card>
              <CardHeader title="Executed and blocked experiments" icon={FlaskConical} />
              <ul className="divide-y divide-rule">
                {exps.data.map((e) => (
                  <motion.li variants={item} key={e.id}>
                    <Link to={`/experiments/${e.id}`} className="flex flex-wrap items-center gap-3 px-4 py-3 transition-colors hover:bg-raised">
                      <StatusBadge status={e.status} />
                      <div className="min-w-0 flex-1">
                        <div className="truncate text-sm text-ink">{e.prediction?.impact?.scenario_label}: {shortId(e.prediction?.target_id || '')}</div>
                        <div className="text-xs text-faint">
                          {e.mode || 'pending'} · accuracy {pct(e.comparison?.metrics?.accuracy)} · {timeAgo(e.created_at)}
                        </div>
                      </div>
                      <ChevronRight className="h-4 w-4 text-faint" />
                    </Link>
                  </motion.li>
                ))}
              </ul>
            </Card>
          )}
          {untested.length > 0 && (
            <Card>
              <CardHeader title="Predictions not yet validated" subtitle="Status: Prediction only" />
              <ul className="divide-y divide-rule">
                {untested.map((p) => (
                  <motion.li variants={item} key={p.id} className="flex flex-wrap items-center gap-3 px-4 py-3">
                    <StatusBadge status="prediction_only" />
                    <div className="min-w-0 flex-1">
                      <div className="truncate text-sm text-ink">{p.impact.scenario_label}: {shortId(p.target_id)}</div>
                      <div className="text-xs text-faint">{p.impact.summary.tests_predicted_fail} tests predicted to fail · {timeAgo(p.created_at)}</div>
                    </div>
                    <Link to={`/lab?prediction=${p.id}`}><Button size="sm">Validate</Button></Link>
                  </motion.li>
                ))}
              </ul>
            </Card>
          )}
        </motion.div>
      )}
    </>
  )
}
