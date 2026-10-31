import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it, vi } from 'vitest'
import WhatIfLab from '../pages/WhatIfLab'

vi.mock('../components/ProjectGate', () => ({
  default: ({ children }) => children({ id: 'p1', latest_run_id: 'run1', trusted: true }),
}))

vi.mock('../hooks/useAsync', () => ({
  useAsync: () => ({ data: [], loading: false }),
}))

vi.mock('../hooks/useStatus', () => ({
  useStatus: () => ({ ai: { docker_sandbox: false } }),
}))

vi.mock('../services/api', () => ({
  api: {
    scenarios: vi.fn().mockResolvedValue([]),
    prediction: vi.fn().mockResolvedValue(null),
    dynamicScenario: vi.fn(),
    createPrediction: vi.fn(),
    startExperiment: vi.fn(),
    experiment: vi.fn(),
  },
}))

import { api } from '../services/api'

const PREDICTION = {
  id: 'pred1',
  scenario: 'db_unavailable',
  target_id: 'database:sqlite',
  impact: {
    scenario_label: 'Database unavailable',
    target: { name: 'sqlite database' },
    fault_description: 'fault',
    summary: { definite: 0, possible: 0, routes_affected: 0, routes_total: 0, tests_predicted_fail: 0, tests_predicted_may_fail: 0, tests_predicted_pass: 0 },
    affected: [],
    tests: [],
    method: 'reverse BFS',
    limitations: [],
  },
}

describe('WhatIfLab dynamic scenario consent', () => {
  it('keeps synthesis disabled until the user consents to sending prompt and evidence', () => {
    render(
      <MemoryRouter future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
        <WhatIfLab />
      </MemoryRouter>
    )
    fireEvent.click(screen.getByRole('button', { name: /gemma 4 dynamic synthesizer/i }))

    fireEvent.change(screen.getByPlaceholderText(/simulate redis cache eviction storm/i), {
      target: { value: 'Database connection pool exhaustion' },
    })
    const synthesizeButton = screen.getByRole('button', { name: /synthesize with gemma 4 & simulate/i })
    expect(synthesizeButton).toBeDisabled()

    const consent = screen.getByRole('checkbox', {
      name: /my prompt and repository evidence will be sent to gemma/i,
    })
    fireEvent.click(consent)
    expect(synthesizeButton).toBeEnabled()

    fireEvent.change(screen.getByPlaceholderText(/simulate redis cache eviction storm/i), {
      target: { value: 'Different scenario' },
    })
    expect(synthesizeButton).toBeDisabled()
  })

  it('sends the real consent value to dynamicScenario only after the checkbox is checked', async () => {
    api.dynamicScenario.mockResolvedValue({ result: { scenario_id: 'db_unavailable', suggested_target_id: 'database:sqlite' } })
    api.createPrediction.mockResolvedValue(PREDICTION)
    render(
      <MemoryRouter future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
        <WhatIfLab />
      </MemoryRouter>
    )
    fireEvent.click(screen.getByRole('button', { name: /gemma 4 dynamic synthesizer/i }))

    const prompt = screen.getByPlaceholderText(/simulate redis cache eviction storm/i)
    fireEvent.change(prompt, { target: { value: 'Database connection pool exhaustion' } })
    fireEvent.click(screen.getByRole('checkbox', { name: /my prompt and repository evidence will be sent to gemma/i }))

    // The button is the only path to the API and it stays disabled while the
    // box is unchecked, so `consent: false` can never reach the backend.
    fireEvent.click(screen.getByRole('button', { name: /synthesize with gemma 4 & simulate/i }))

    await waitFor(() => {
      expect(api.dynamicScenario).toHaveBeenCalledWith('p1', { prompt: 'Database connection pool exhaustion', consent: true })
    })
  })
})
