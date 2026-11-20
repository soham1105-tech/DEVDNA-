import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { api } from '../services/api'
import ChangeIntelligence from '../pages/ChangeIntelligence'

vi.mock('../services/api', () => ({
  api: {
    listChangeAnalyses: vi.fn(),
    getChangeAnalysis: vi.fn(),
    runChangeDemo: vi.fn(),
    analyzeChange: vi.fn(),
    validateChangeAnalysis: vi.fn(),
  },
}))

vi.mock('../hooks/useProject', () => ({
  useProject: () => ({
    current: { id: 'p1', name: 'ShopLite Demo', latest_run_id: 'r1' },
    project: { id: 'p1', name: 'ShopLite Demo', latest_run_id: 'r1' },
    loading: false,
    ready: true,
  }),
}))

const MOCK_ANALYSIS = {
  id: 'ca_test1',
  project_id: 'p1',
  title: 'Change in shoplite/db.py',
  base_ref: 'main',
  head_ref: 'feature/db-pool',
  created_at: new Date().toISOString(),
  recommendation: {
    decision: 'REVIEW REQUIRED',
    headline: 'Meaningful risks, route exposure, or untested paths require engineering review.',
    recommended_actions: ['Run prioritized tests', 'Verify integration behavior for exposed API routes.'],
    blockers: [],
    evidence: [
      { type: 'risk_assessment', title: 'Risk Score: 45/100 (MEDIUM)', description: 'Change impacts components' },
    ],
  },
  risk_assessment: {
    score: 45,
    risk_score: 45,
    level: 'medium',
    factors: [
      { name: 'Blast Radius', raw_score: 50, weight: 0.25, contribution: 12.5, reason: '4 components impacted' },
      { name: 'Component Criticality', raw_score: 60, weight: 0.20, contribution: 12.0, reason: 'Database component' },
    ],
    uncertainty: { score: 10, level: 'low', limitations: ['Static call graph approximation'] },
  },
  diff_manifest: {
    files: [
      {
        path: 'shoplite/db.py',
        change_type: 'modified',
        additions: 12,
        deletions: 3,
        components_changed: ['func:get_db', 'func:init_db'],
      },
    ],
    summary: { total_files: 1, additions: 12, deletions: 3, changed_functions: 2 },
  },
  graph_diff: {
    directly_affected: [{ id: 'func:get_db', name: 'get_db', file: 'shoplite/db.py', line: 10 }],
    transitively_affected: [{ id: 'route:get_products', name: 'get_products', file: 'shoplite/routes.py', distance: 1 }],
    potentially_affected: [],
    impacted_routes: [{ id: 'r1', route: 'GET /api/products', file: 'shoplite/routes.py', distance: 2 }],
    associated_tests: ['tests/test_api.py::test_products'],
    critical_components: [{ id: 'func:get_db', name: 'get_db' }],
    new_dependencies: [],
    removed_dependencies: [],
    summary: { directly_affected_count: 1, transitively_affected_count: 1, impacted_routes_count: 1, associated_tests_count: 1 },
  },
  prioritized_tests: {
    tests: [
      {
        id: 'test_1',
        name: 'tests/test_api.py::test_products',
        tier: 2,
        tier_name: 'Direct Dependents',
        selection_reason: 'Exercises route calling modified component',
        related_components: ['func:get_db'],
        priority_score: 85,
        coverage_confidence: 'high',
      },
    ],
    summary: { total_prioritized_tests: 1, tier_1_direct: 0, tier_2_direct_dependents: 1 },
  },
  validation: {
    executed: true,
    fault: { kind: 'db_unavailable', target: 'database:sqlite' },
    baseline_summary: { passed: 10, failed: 0 },
    faulted_summary: { passed: 8, failed: 2 },
    accuracy: 0.9,
    limitations: ['Harness restricted to mock faults'],
  },
  ai_explanation: {
    summary: 'Change alters database connection handling.',
    propagation_narrative: 'Modifying get_db cascades to product and order routes.',
    failure_risks: [{ component: 'get_db', risk: 'Connection pool exhaustion' }],
    suggested_repairs: ['Ensure proper timeout on pool acquisition'],
  },
}

describe('ChangeIntelligence page', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    api.listChangeAnalyses.mockResolvedValue([MOCK_ANALYSIS])
    api.getChangeAnalysis.mockResolvedValue(MOCK_ANALYSIS)
    api.runChangeDemo.mockResolvedValue(MOCK_ANALYSIS)
  })

  it('renders all 6 navigation tabs', async () => {
    render(
      <MemoryRouter future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
        <ChangeIntelligence />
      </MemoryRouter>,
    )

    await waitFor(() => {
      expect(screen.getByText('Change Overview')).toBeInTheDocument()
    })
    expect(screen.getByText('Impact Graph')).toBeInTheDocument()
    expect(screen.getByText('Risk Breakdown')).toBeInTheDocument()
    expect(screen.getByText('Test Intelligence')).toBeInTheDocument()
    expect(screen.getByText('Validation Report')).toBeInTheDocument()
    expect(screen.getByText('AI Explanation')).toBeInTheDocument()
  })

  it('displays the merge recommendation and risk score', async () => {
    render(
      <MemoryRouter future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
        <ChangeIntelligence />
      </MemoryRouter>,
    )

    await waitFor(() => {
      expect(screen.getByText('REVIEW REQUIRED')).toBeInTheDocument()
    })
    expect(screen.getByText(/Meaningful risks, route exposure, or untested paths/i)).toBeInTheDocument()
    expect(screen.getByText('45')).toBeInTheDocument()
  })

  it('switches between tabs and displays content', async () => {
    render(
      <MemoryRouter future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
        <ChangeIntelligence />
      </MemoryRouter>,
    )

    await waitFor(() => {
      expect(screen.getByText('REVIEW REQUIRED')).toBeInTheDocument()
    })

    // Switch to Impact Graph tab
    fireEvent.click(screen.getByText('Impact Graph'))
    expect(screen.getByText('GET /api/products')).toBeInTheDocument()

    // Switch to Test Intelligence tab
    fireEvent.click(screen.getByText('Test Intelligence'))
    expect(screen.getByText('tests/test_api.py::test_products')).toBeInTheDocument()

    // Switch to AI Explanation tab
    fireEvent.click(screen.getByText('AI Explanation'))
    expect(screen.getByText(/Modifying get_db cascades to product and order routes/)).toBeInTheDocument()
  })
})
