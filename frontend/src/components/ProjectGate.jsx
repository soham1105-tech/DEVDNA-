import { FolderSearch, Loader2, ServerCrash } from 'lucide-react'
import { useOutletContext } from 'react-router-dom'
import { useProject } from '../hooks/useProject'
import { Button, EmptyState, ErrorState, SkeletonCards } from './ui'

/** Renders children only when a project with a completed analysis is selected. */
export default function ProjectGate({ children }) {
  const { current, loading, error, refresh } = useProject()
  const ctx = useOutletContext() || {}
  if (loading) return <SkeletonCards count={4} />
  if (error) return <ErrorState title="Backend unavailable" error={error} onRetry={refresh} />
  if (!current)
    return (
      <EmptyState
        icon={FolderSearch}
        title="No project selected"
        description="Analyse a repository or explore the bundled sample project to get started."
        action={<Button variant="primary" onClick={ctx.openNewProject}>New project</Button>}
      />
    )
  const run = current.latest_run
  if (!current.latest_run_id) {
    return <EmptyState icon={Loader2} title="Analysis not finished" description="This project has not completed an analysis yet." action={<Button onClick={refresh}>Refresh</Button>} />
  }
  if (run?.status === 'failed') return <EmptyState icon={ServerCrash} title="Analysis failed" description={run.error} />
  return children(current)
}
