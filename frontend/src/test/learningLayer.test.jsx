import { fireEvent, render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it } from 'vitest'
import ExplainAnalysisButton from '../components/ExplainAnalysisButton'
import TermExplainer from '../components/TermExplainer'
import { GLOSSARY_CATEGORIES, GLOSSARY_TERMS, getTerm, searchGlossary } from '../data/glossary'
import { SettingsProvider } from '../hooks/useSettings'
import EngineeringGuide from '../pages/EngineeringGuide'
import { buildAnalysisExplanation } from '../utils/analysisExplainer'

function wrap(ui, route = '/guide') {
  return render(
    <SettingsProvider>
      <MemoryRouter initialEntries={[route]} future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
        {ui}
      </MemoryRouter>
    </SettingsProvider>,
  )
}

describe('Structured Glossary & Search', () => {
  it('covers all 5 required categories and core engineering terms', () => {
    expect(GLOSSARY_CATEGORIES.map((c) => c.label)).toEqual([
      'Code Analysis',
      'Dependency Intelligence',
      'Risk Analysis',
      'Testing & Validation',
      'AI & Reliability',
    ])
    for (const id of [
      'ast', 'parsing', 'static_analysis', 'git_diff',
      'dependency_graph', 'reverse_bfs', 'traversal', 'blast_radius',
      'criticality', 'betweenness_centrality', 'risk_scoring',
      'baseline', 'fault_injection', 'regression_testing', 'experiment_validation',
      'llm', 'grounded_explanation', 'false_positives', 'precision', 'recall',
    ]) {
      const t = getTerm(id)
      expect(t).toBeTruthy()
      expect(t.simple).toBeTruthy()
      expect(t.technical).toBeTruthy()
      expect(t.whyItMatters).toBeTruthy()
    }
  })

  it('filters glossary by search query, aliases, and category', () => {
    expect(searchGlossary('Dual BFS').some((t) => t.id === 'reverse_bfs')).toBe(true)
    expect(searchGlossary('', 'risk_analysis').every((t) => t.category === 'risk_analysis')).toBe(true)
    expect(searchGlossary('nonexistent-xyz-term')).toEqual([])
    expect(searchGlossary('').length).toBe(GLOSSARY_TERMS.length)
  })
})

describe('Contextual TermExplainer & Learning Modes', () => {
  it('opens on click, switches between Beginner and Technical modes, and navigates related concepts', () => {
    wrap(<TermExplainer termId="criticality">Criticality</TermExplainer>)

    fireEvent.click(screen.getByRole('button', { name: /explain criticality/i }))
    expect(screen.getByText(/implemented formula/i)).toBeInTheDocument()
    expect(screen.getByText(/0\.5 × blast/i)).toBeInTheDocument()

    // Switch to Beginner mode
    fireEvent.click(screen.getByRole('radio', { name: 'Beginner' }))
    expect(screen.getByText(/in plain english/i)).toBeInTheDocument()
    expect(screen.getByText(/everyday analogy/i)).toBeInTheDocument()

    // Navigate to related concept
    fireEvent.click(screen.getByRole('button', { name: 'Blast Radius' }))
    expect(screen.getByRole('heading', { name: 'Blast Radius' })).toBeInTheDocument()
  })
})

describe('Engineering Guide Page', () => {
  it('supports searching terms, filtering by category, and opening related concepts', () => {
    wrap(<EngineeringGuide />)

    const search = screen.getByLabelText(/search engineering guide/i)
    fireEvent.change(search, { target: { value: 'precision' } })
    expect(screen.getByRole('option', { name: /precision/i })).toBeInTheDocument()

    fireEvent.change(search, { target: { value: '' } })
    fireEvent.click(screen.getByRole('tab', { name: 'Risk Analysis' }))
    expect(screen.getByRole('option', { name: /betweenness centrality/i })).toBeInTheDocument()
  })
})

describe('Explain This Analysis Engine & Guardrails', () => {
  it('handles missing or unavailable evidence honestly without inventing metrics', () => {
    const exp = buildAnalysisExplanation('prediction', null, 'technical')
    expect(exp.badge).toMatch(/requires data/i)
    expect(exp.evidence[0]).toMatch(/requires verification/i)
  })

  it('does not mutate underlying analysis data when switching between Beginner and Technical modes', () => {
    const prediction = {
      id: 'p1',
      scenario: 'db_unavailable',
      target_id: 'database:sqlite',
      impact: {
        scenario_label: 'Database unavailable',
        target: { name: 'sqlite database' },
        summary: { definite: 6, possible: 2, routes_affected: 4, routes_total: 6, tests_predicted_fail: 5, tests_predicted_may_fail: 1 },
      },
    }
    const snapshot = JSON.stringify(prediction)

    const beginner = buildAnalysisExplanation('prediction', prediction, 'beginner')
    const technical = buildAnalysisExplanation('prediction', prediction, 'technical')

    expect(JSON.stringify(prediction)).toBe(snapshot)
    expect(beginner.whatCalculated).not.toEqual(technical.whatCalculated)
    expect(technical.whatCalculated).toMatch(/Dual Reverse BFS/i)
  })

  it('never claims a blocked or unexecuted experiment succeeded', () => {
    const blockedLab = buildAnalysisExplanation(
      'what_if_lab',
      { mode: 'blocked', step: 3, prediction: null, experiment: { status: 'blocked' } },
      'technical',
    )
    expect(blockedLab.badge).toMatch(/not yet validated/i)
    expect(blockedLab.simpleMeaning).toMatch(/no runtime validation has succeeded/i)
    expect(blockedLab.evidence.join(' ')).toMatch(/no project code was executed/i)

    const blockedChange = buildAnalysisExplanation(
      'change_intelligence',
      {
        title: 'PR #10',
        risk_assessment: { score: 42, level: 'medium' },
        graph_diff: { summary: { directly_affected_count: 1, transitively_affected_count: 3, impacted_routes_count: 1 } },
        prioritized_tests: { summary: { total_prioritized_tests: 2, tier_1_direct: 1 } },
        recommendation: { decision: 'REVIEW REQUIRED', recommended_actions: [] },
        validation: { executed: false, status: 'blocked', reason: 'DEVDNA_EXPERIMENTS_ENABLED=false' },
      },
      'beginner',
    )
    expect(blockedChange.badge).toBe('Static prediction only')
    expect(blockedChange.evidence.join(' ')).toMatch(/BLOCKED/i)
    expect(blockedChange.evidence.join(' ')).toMatch(/no project code executed/i)
  })

  it('renders ExplainAnalysisButton modal with all 5 structured sections', () => {
    wrap(
      <ExplainAnalysisButton
        kind="findings"
        data={{ items: [{ severity: 'high', is_suppressed: false }] }}
      />,
    )

    fireEvent.click(screen.getByRole('button', { name: /explain this analysis/i }))
    expect(screen.getByText(/1\. what was calculated/i)).toBeInTheDocument()
    expect(screen.getByText(/3\. evidence from this project/i)).toBeInTheDocument()
    expect(screen.getByText(/4\. why it matters/i)).toBeInTheDocument()
    expect(screen.getByText(/5\. what to investigate next/i)).toBeInTheDocument()
  })
})
