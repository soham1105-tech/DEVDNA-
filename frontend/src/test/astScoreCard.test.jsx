import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import AstScoreCard from '../components/AstScoreCard'

describe('AstScoreCard', () => {
  it('shows a loading state while the repository is being analyzed', () => {
    render(
      <AstScoreCard
        ast={{ status: 'loading', data: null, error: null }}
        repository={{ owner: 'example', repo: 'sample', branch: 'main' }}
      />,
    )

    expect(screen.getByText('Analyzing AST structure...')).toBeInTheDocument()
    expect(screen.getByText('example/sample@main')).toBeInTheDocument()
  })

  it('shows the score, analyzed file count, and AI warning on success', () => {
    render(
      <AstScoreCard
        ast={{
          status: 'success',
          data: { overall_architect_score: 87.25, is_ai_flagged: true, analyzed_files: 12 },
          error: null,
        }}
        repository={{ owner: 'example', repo: 'sample', branch: 'main' }}
      />,
    )

    expect(screen.getByText('87% Verified Human')).toBeInTheDocument()
    expect(screen.getByText('12 Python files analyzed · example/sample@main')).toBeInTheDocument()
    expect(screen.getByText('AI Boilerplate Flagged')).toBeInTheDocument()
  })

  it('shows N/A and the connection error when the engine is unavailable', () => {
    render(
      <AstScoreCard
        ast={{ status: 'error', data: null, error: new Error('AST Engine is offline') }}
        repository={{ owner: 'example', repo: 'sample', branch: 'main' }}
      />,
    )

    expect(screen.getByText('AST Score: N/A')).toBeInTheDocument()
    expect(screen.getByText('AST Engine is offline')).toBeInTheDocument()
  })

  it('explains why the score is unavailable when no repository is linked', () => {
    render(<AstScoreCard ast={{ status: 'unavailable', data: null, error: null }} repository={null} />)

    expect(screen.getByText('AST Score: N/A')).toBeInTheDocument()
    expect(screen.getByText(/Add a GitHub repository/)).toBeInTheDocument()
  })
})