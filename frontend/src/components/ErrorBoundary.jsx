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
        <main className="flex min-h-screen items-center justify-center bg-ink-950 p-6 text-ink-100">
          <div className="max-w-md space-y-4 text-center">
            <h1 className="text-xl font-semibold">This page ran into a problem</h1>
            <p className="text-sm text-ink-300">You can try rendering the page again or reload the app.</p>
            <div className="flex justify-center gap-3">
              <button
                type="button"
                onClick={this.reset}
                className="rounded-lg bg-violet-500 px-4 py-2 text-sm font-medium text-white hover:bg-violet-400"
              >
                Try again
              </button>
              <button
                type="button"
                onClick={() => window.location.reload()}
                className="rounded-lg border border-ink-600 px-4 py-2 text-sm font-medium text-ink-100 hover:bg-ink-800"
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
