import { AlertTriangle, ShieldCheck } from 'lucide-react'
import { Card } from './ui'

export default function AstScoreCard({ ast, repository }) {
  const score = ast.data?.overall_architect_score
  const scoreText = score === null || score === undefined ? 'AST Score: N/A' : `${Math.round(score)}% Verified Human`

  return (
    <Card className="col-span-2 p-4 lg:col-span-1">
      <div className="flex items-center justify-between gap-2">
        <span className="text-xs font-medium uppercase tracking-wider text-ink-300">AST Authenticity</span>
        <span className="flex h-7 w-7 items-center justify-center rounded-md bg-emerald-400/10 text-emerald-300">
          <ShieldCheck className="h-3.5 w-3.5" />
        </span>
      </div>
      <div className="mt-2 text-lg font-semibold leading-6 tabular-nums text-ink-100">
        {ast.status === 'loading' ? 'Analyzing AST structure...' : scoreText}
      </div>
      <div className="mt-1 text-xs text-ink-300">
        {ast.status === 'success'
          ? `${ast.data.analyzed_files} Python files analyzed · ${repository.owner}/${repository.repo}@${repository.branch}`
          : ast.status === 'loading'
          ? `${repository.owner}/${repository.repo}@${repository.branch}`
          : ast.status === 'error'
          ? ast.error.message
          : 'Add a GitHub repository when creating a project to enable AST verification.'}
      </div>
      {ast.data?.is_ai_flagged && (
        <div className="mt-2 inline-flex items-center gap-1.5 rounded-md border border-amber-400/25 bg-amber-400/10 px-2 py-1 text-xs font-medium text-amber-200">
          <AlertTriangle className="h-3.5 w-3.5" />
          AI Boilerplate Flagged
        </div>
      )}
    </Card>
  )
}