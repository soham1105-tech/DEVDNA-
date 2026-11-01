import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import * as apiModule from '../services/api'
import Insights from '../pages/Insights'

vi.mock('../services/api', async (importOriginal) => ({
  ...(await importOriginal()),
  api: { ...(await importOriginal()).api },
}))

function renderPage() {
  return render(
    <MemoryRouter>
      <Insights __project={null} />
    </MemoryRouter>,
  )
}

// Insights renders through ProjectGate which needs a project; stub the gate.
vi.mock('../components/ProjectGate', () => ({
  default: ({ children }) =>
    children({
      id: 'p1',
      name: 'Demo',
      latest_run_id: 'r1',
    }),
}))

describe('Insights page', () => {
  beforeEach(() => {
    vi.restoreAllMocks()
  })

  it('shows the three insight tabs and the PR-review form by default', () => {
    renderPage()
    expect(screen.getByText('PR Review')).toBeInTheDocument()
    expect(screen.getByText('Test Gaps')).toBeInTheDocument()
    expect(screen.getByText('Reliability')).toBeInTheDocument()
    expect(screen.getByPlaceholderText(/backend\/app\/graph\/engine\.py/)).toBeInTheDocument()
  })

  it('renders the test-gap map with honest validation status', async () => {
    vi.spyOn(apiModule.api, 'testGaps').mockResolvedValue({
      test_count: 246,
      file_gaps: [{ file: 'app/cli.py', components: 9, covered: 3, uncovered: 6, gap_ids: [], min_distance: 1 }],
      fully_covered_files: ['app/db.py'],
      uncovered_by_type: { function: 6 },
      summary: { code_components: 100, covered_components: 94, uncovered_components: 6, files_with_gaps: 1, files_fully_covered: 1 },
      validation: { status: 'static_only', experiments_observed: 0, tests_observed_executing: 0, files_validated: 0, note: 'No completed experiment has executed this project\'s tests.' },
      method: 'forward BFS',
      limitations: ['Static reachability from a test is not proof the test asserts meaningful behaviour.'],
    })
    renderPage()
    const tab = await screen.findByText('Test Gaps')
    tab.click()
    expect(await screen.findByText('app/cli.py')).toBeInTheDocument()
    expect(screen.getByText('6/9 unreachable')).toBeInTheDocument()
    expect(screen.getByText(/No completed experiment/)).toBeInTheDocument()
  })

  it('renders the honest empty state when no reliability memory exists', async () => {
    vi.spyOn(apiModule.api, 'reliability').mockResolvedValue({
      status: 'empty',
      project_id: 'p1',
      note: 'No experiment observations are recorded for this project. Run controlled experiments to build a reliability memory.',
      summary: { observations: 0, experiments: 0, first_observed_at: null, last_observed_at: null },
      by_scenario: [],
      by_pattern: [],
      method: 'aggregation',
      limitations: ['Memory covers only claims that were experimentally observed.'],
    })
    renderPage()
    const tab = await screen.findByText('Reliability')
    tab.click()
    expect(await screen.findByText('No experiment observations recorded yet')).toBeInTheDocument()
    expect(screen.getByText(/Run controlled experiments/)).toBeInTheDocument()
  })
})
