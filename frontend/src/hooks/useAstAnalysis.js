import { useEffect, useState } from 'react'
import { analyzeRepository } from '../services/astEngine'

export function useAstAnalysis(repository) {
  const owner = repository?.owner
  const repo = repository?.repo
  const branch = repository?.branch || 'main'
  const [result, setResult] = useState(() => ({
    status: owner && repo ? 'loading' : 'unavailable',
    data: null,
    error: null,
  }))

  useEffect(() => {
    if (!owner || !repo) {
      setResult({ status: 'unavailable', data: null, error: null })
      return undefined
    }

    const controller = new AbortController()
    setResult({ status: 'loading', data: null, error: null })
    analyzeRepository({ owner, repo, branch, signal: controller.signal })
      .then((data) => setResult({ status: 'success', data, error: null }))
      .catch((error) => {
        if (error?.name !== 'AbortError') setResult({ status: 'error', data: null, error })
      })

    return () => controller.abort()
  }, [owner, repo, branch])

  return result
}
