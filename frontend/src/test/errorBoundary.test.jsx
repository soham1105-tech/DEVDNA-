import { fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import ErrorBoundary from '../components/ErrorBoundary'

afterEach(() => vi.restoreAllMocks())

describe('ErrorBoundary', () => {
  it('shows a safe fallback for render errors and can retry rendering', () => {
    let shouldThrow = true
    function ThrowingComponent() {
      if (shouldThrow) throw new Error('sensitive API payload')
      return <p>Page recovered</p>
    }

    vi.spyOn(console, 'error').mockImplementation(() => {})
    render(
      <ErrorBoundary>
        <ThrowingComponent />
      </ErrorBoundary>
    )

    expect(screen.getByText('This page ran into a problem')).toBeInTheDocument()
    expect(screen.getByText('You can try rendering the page again or reload the app.')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Try again' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Reload page' })).toBeInTheDocument()
    expect(screen.queryByText('sensitive API payload')).not.toBeInTheDocument()

    shouldThrow = false
    fireEvent.click(screen.getByRole('button', { name: 'Try again' }))
    expect(screen.getByText('Page recovered')).toBeInTheDocument()
  })
})
