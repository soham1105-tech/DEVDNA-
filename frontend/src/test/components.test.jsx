import { fireEvent, render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'
import ConsentModal from '../components/ConsentModal'
import ImpactView from '../components/ImpactView'
import MultimodalAuditModal from '../components/MultimodalAuditModal'
import { StatusBadge } from '../components/ui'
import { SettingsProvider } from '../hooks/useSettings'
import { ThemeToggle } from '../layouts/AppLayout'

afterEach(() => vi.unstubAllGlobals())

const preview = {
  model: 'gemma-4-26b-a4b-it',
  configured: true,
  evidence: { total_chars: 42, items: [{ ref: 'E1', kind: 'scenario', title: 'Database unavailable', content: 'redacted evidence' }] },
}

describe('ConsentModal', () => {
  it('only allows sending after explicit agreement', () => {
    const onConfirm = vi.fn()
    render(<ConsentModal open preview={preview} purpose="Failure analysis" onCancel={() => {}} onConfirm={onConfirm} />)
    const send = screen.getByRole('button', { name: /send to gemma/i })
    expect(send).toBeDisabled()
    fireEvent.click(screen.getByLabelText(/i agree/i))
    expect(send).toBeEnabled()
    fireEvent.click(send)
    expect(onConfirm).toHaveBeenCalledWith(false)
  })

  it('shows the exact evidence and the model name', () => {
    render(<ConsentModal open preview={preview} onCancel={() => {}} onConfirm={() => {}} />)
    expect(screen.getByText('E1')).toBeInTheDocument()
    expect(screen.getByText(/1 evidence items/)).toBeInTheDocument()
    fireEvent.click(screen.getByText('Database unavailable'))
    expect(screen.getByText('redacted evidence')).toBeInTheDocument()
  })
})

describe('StatusBadge', () => {
  it('labels experiment statuses honestly', () => {
    render(<><StatusBadge status="prediction_confirmed" /><StatusBadge status="blocked" /><StatusBadge status="prediction_only" /></>)
    expect(screen.getByText('Prediction confirmed')).toBeInTheDocument()
    expect(screen.getByText('Blocked')).toBeInTheDocument()
    expect(screen.getByText('Prediction only')).toBeInTheDocument()
  })
})

describe('ImpactView', () => {
  const prediction = {
    id: 'p1',
    impact: {
      summary: { definite: 1, possible: 1, routes_affected: 1, routes_total: 2, tests_predicted_fail: 1, tests_predicted_may_fail: 0 },
      affected: [
        { id: 'func:app.db.get', type: 'function', certainty: 'definite', distance: 1, path: ['func:app.db.get', 'database:sqlite'], handled_at: null },
        { id: 'func:app.health', type: 'function', certainty: 'possible', distance: 2, path: ['func:app.health', 'func:app.db.get', 'database:sqlite'], handled_at: { file: 'app/h.py', line: 3, handles: ['sqlite3.Error'] } },
      ],
      tests: [{ id: 't', nodeid: 'tests/test_a.py::test_a', prediction: 'fail' }],
      method: 'reverse BFS',
      limitations: ['static only'],
    },
  }
  it('shows certainty, expandable paths and handled errors', () => {
    render(
      <MemoryRouter future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
        <ImpactView prediction={prediction} />
      </MemoryRouter>
    )
    expect(screen.getByText('Definite')).toBeInTheDocument()
    expect(screen.getByText('Possible')).toBeInTheDocument()
    expect(screen.getByText('Predicted fail')).toBeInTheDocument()
    fireEvent.click(screen.getByText('app.health'))
    expect(screen.getByText(/Error handled at app\/h.py:3/)).toBeInTheDocument()
    expect(screen.getByRole('link', { name: /view in graph/i })).toHaveAttribute('href', '/architecture?prediction=p1')
  })
})

describe('ThemeToggle', () => {
  it('toggles between dark and bright theme and sets document dataset', () => {
    render(
      <SettingsProvider>
        <ThemeToggle />
      </SettingsProvider>
    )
    const btn = screen.getByRole('button', { name: /switch to bright mode/i })
    expect(btn).toBeInTheDocument()
    expect(document.documentElement.dataset.theme).toBe('dark')

    fireEvent.click(btn)
    expect(document.documentElement.dataset.theme).toBe('bright')
    expect(screen.getByRole('button', { name: /switch to dark mode/i })).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: /switch to dark mode/i }))
    expect(document.documentElement.dataset.theme).toBe('dark')
  })
})

describe('MultimodalAuditModal', () => {
  it('requires explicit consent before enabling the audit button', () => {
    vi.stubGlobal('URL', {
      createObjectURL: vi.fn(() => 'blob:diagram'),
      revokeObjectURL: vi.fn(),
    })
    const { unmount } = render(<MultimodalAuditModal open onClose={() => {}} projectId="p1" />)
    const auditButton = screen.getByRole('button', { name: /analyze diagram with gemma 4/i })
    fireEvent.change(document.body.querySelector('input[type="file"]'), {
      target: { files: [new File(['image'], 'diagram.png', { type: 'image/png' })] },
    })
    expect(auditButton).toBeDisabled()

    fireEvent.click(screen.getByRole('checkbox', { name: /i understand this diagram and notes will be sent to gemma/i }))
    expect(auditButton).toBeEnabled()
    unmount()
  })
})
