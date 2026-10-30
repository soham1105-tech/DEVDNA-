import { ArrowLeft } from 'lucide-react'
import { Link, useParams } from 'react-router-dom'
import PredictionVsReality from '../components/PredictionVsReality'
import { Button, ErrorState, PageHeader, Skeleton } from '../components/ui'
import { useAsync, usePoll } from '../hooks/useAsync'
import { api } from '../services/api'
import { timeAgo } from '../utils/format'
import { shortId } from '../utils/graph'

export default function ExperimentDetail() {
  const { id } = useParams()
  const { data, error, loading, reload, setData } = useAsync(() => api.experiment(id), [id])
  usePoll(() => reload({ silent: true }), 1500, data && ['queued', 'running'].includes(data.status))

  return (
    <>
      <Link to="/experiments" className="mb-3 inline-block"><Button variant="ghost" size="sm" icon={ArrowLeft}>All experiments</Button></Link>
      {loading && !data && <Skeleton className="h-96" />}
      {error && <ErrorState error={error} onRetry={reload} />}
      {data && (
        <>
          <PageHeader
            title={`${data.prediction?.impact?.scenario_label} → ${shortId(data.prediction?.target_id || '')}`}
            subtitle={`Approved ${timeAgo(data.approved_at)} · finished ${timeAgo(data.finished_at)} · id ${data.id}`}
          />
          {['queued', 'running'].includes(data.status) ? <Skeleton className="h-64" /> : <PredictionVsReality experiment={data} onUpdate={setData} />}
        </>
      )}
    </>
  )
}
