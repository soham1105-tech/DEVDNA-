import { fireEvent, render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it, vi } from 'vitest'
import ScenarioPicker from '../components/ScenarioPicker'
import Prediction from '../pages/Prediction'

const SCENARIOS = [
  {
    id: 'db_unavailable',
    label: 'Database unavailable',
    fault: 'Every database connection attempt raises an OperationalError.',
    target_types: ['database'],
    supported: true,
    targets: [
      {
        id: 'database:sqlite',
        name: 'sqlite database',
        type: 'database',
        criticality: 1.0,
        dependents: 11,
        routes_depending: 0,
        tests_covering: 4,
        non_test_total: 550,
        routes_total: 12,
      },
    ],
  },
  {
    id: 'external_api_unavailable',
    label: 'External API unavailable',
    fault: 'Outbound HTTP transports raise a connection error.',
    target_types: ['external_api'],
    supported: false,
    targets: [],
  },
]

const HISTORY = [
  {
    id: 'pred_hist_1',
    scenario: 'db_unavailable',
    target_id: 'database:sqlite',
    created_at: new Date().toISOString(),
    ai_explanation: null,
    impact: {
      scenario_label: 'Database unavailable',
      target: { name: 'sqlite database' },
      fault_description: 'Every database connection attempt raises an OperationalError.',
      summary: { definite: 8, possible: 3, routes_affected: 6, routes_total: 6, tests_predicted_fail: 8, tests_predicted_may_fail: 0, tests_predicted_pass: 0 },
      affected: [],
      tests: [],
      method: 'reverse BFS',
      limitations: [],
    },
  },
]

vi.mock('../components/ProjectGate', () => ({
  default: ({ children }) => children({ id: 'p1', name: 'Flask', latest_run_id: 'r1', trusted: false }),
}))

vi.mock('../hooks/useAsync', () => ({
  useAsync: (fn) => {
    const src = String(fn)
    if (src.includes('scenarios')) return { data: SCENARIOS, loading: false, error: null, reload: vi.fn() }
    return { data: HISTORY, loading: false, error: null, reload: vi.fn() }
  },
}))

vi.mock('../services/api', () => ({
  api: {
    scenarios: vi.fn(),
    predictions: vi.fn(),
    prediction: vi.fn(),
    createPrediction: vi.fn(),
    explainPrediction: vi.fn(),
  },
}))

describe('ScenarioPicker & Prediction UX', () => {
  it('displays 0-100 criticality scale, low-score explanation for leaf resources, and enable hints for unavailable scenarios', () => {
    render(
      <MemoryRouter future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
        <ScenarioPicker
          scenarios={SCENARIOS}
          scenario="db_unavailable"
          target="database:sqlite"
          onScenario={vi.fn()}
          onTarget={vi.fn()}
        />
      </MemoryRouter>,
    )

    expect(screen.getByText('1/100')).toBeInTheDocument()
    expect(screen.getByText(/0–100 scale/i)).toBeInTheDocument()
    expect(screen.getByText(/leaf resources like databases have 0 betweenness/i)).toBeInTheDocument()
    expect(screen.getByText('Unavailable')).toBeInTheDocument()
    expect(screen.getByText(/add outbound http client calls/i)).toBeInTheDocument()
  })

  it('renders static analysis preview, history metadata, and clear distinction from What-If Lab', () => {
    render(
      <MemoryRouter future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
        <Prediction />
      </MemoryRouter>,
    )

    expect(screen.getByText('Static analysis')).toBeInTheDocument()
    expect(screen.getByText(/static impact analysis preview/i)).toBeInTheDocument()
    expect(screen.getByText(/11 affected \(8 definite\)/i)).toBeInTheDocument()
    expect(screen.getByText('Static only')).toBeInTheDocument()
  })
})
