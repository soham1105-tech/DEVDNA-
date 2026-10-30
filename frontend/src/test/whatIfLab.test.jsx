import { fireEvent, render, screen } from '@testing-library/react'
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
})
