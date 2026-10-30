import { Component } from 'react'

export default class ErrorBoundary extends Component {
  state = { hasError: false }

  static getDerivedStateFromError() {
    return { hasError: true }
  }

  componentDidCatch(error, info) {
    console.error('Uncaught application render error:', error, info)
  }

  reset = () => {
    this.setState({ hasError: false })
  }

  render() {
    if (this.state.hasError) {
      return (
        <main className="flex min-h-screen items-center justify-center bg-ground p-6 text-ink">
          <div className="max-w-md space-y-4 text-center">
            <h1 className="text-xl font-semibold">This page ran into a problem</h1>
            <p className="text-sm text-muted">You can try rendering the page again or reload the app.</p>
            <div className="flex justify-center gap-3">
              <button
                type="button"
                onClick={this.reset}
                className="rounded-control bg-ink px-4 py-2 text-sm font-medium text-ground hover:opacity-90"
              >
                Try again
              </button>
              <button
                type="button"
                onClick={() => window.location.reload()}
                className="rounded-control border border-control px-4 py-2 text-sm font-medium text-ink hover:bg-raised"
              >
                Reload page
              </button>
            </div>
          </div>
        </main>
      )
    }

    return this.props.children
  }
}
