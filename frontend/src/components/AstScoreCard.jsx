import { AlertTriangle } from 'lucide-react'

/** The AST complexity figure, rendered as one cell of the Overview KPI strip. */
export default function AstScoreCard({ ast, repository }) {
  const score = ast.data?.overall_architect_score
  const scoreText = score === null || score === undefined ? 'AST Score: N/A' : `Complexity score: ${Math.round(score)}`

  return (
    <div className="col-span-2 min-w-0 px-4 py-3.5 sm:col-span-1">
      <div className="flex items-center gap-1.5 text-xs text-muted">
        {ast.data?.is_ai_flagged && <AlertTriangle className="h-3.5 w-3.5 text-warning" />}
        <span className="truncate">AST Complexity</span>
      </div>
      <div className="num mt-1 text-2xl leading-7 font-medium text-ink">
        {ast.status === 'loading' ? 'Analyzing AST structure...' : scoreText}
      </div>
      <div className="mt-1 text-xs text-faint">
        Heuristic only, from functions/classes/branches/depth, first 30 Python files — not AI detection.
      </div>
      <div className="mt-1 text-xs text-faint">
        {ast.status === 'success'
          ? `${ast.data.analyzed_files} Python files analyzed · ${repository.owner}/${repository.repo}@${repository.branch}`
          : ast.status === 'loading'
          ? `${repository.owner}/${repository.repo}@${repository.branch}`
          : ast.status === 'error'
          ? ast.error.message
          : 'Add a GitHub repository when creating a project to enable AST complexity scoring.'}
      </div>
      {ast.data?.is_ai_flagged && (
        <div className="mt-1.5 inline-flex items-center gap-1.5 rounded-full bg-warning-bg px-2 py-0.5 text-[11px] font-medium text-warning-ink">
          Low complexity — review manually
        </div>
      )}
    </div>
  )
}
