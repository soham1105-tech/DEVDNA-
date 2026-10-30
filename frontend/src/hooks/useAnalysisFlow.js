import { useCallback, useRef, useState } from 'react'
import { api } from '../services/api'
import { useProject } from './useProject'

const sleep = (ms) => new Promise((r) => setTimeout(r, ms))

/** Creates a project (sample / zip / local / re-analyse) and follows the real analysis run until it finishes. */
export function useAnalysisFlow() {
  const { refresh, setCurrentId } = useProject()
  const [state, setState] = useState({ busy: false, run: null, error: null, project: null, upload: 0 })
  const cancelled = useRef(false)

  const follow = useCallback(async (project, run) => {
    setState((s) => ({ ...s, project, run }))
    let current = run
    while (!cancelled.current && !['completed', 'failed'].includes(current.status)) {
      await sleep(450)
      current = await api.getRun(run.id)
      setState((s) => ({ ...s, run: current }))
    }
    if (current.status === 'failed') throw { code: 'analysis_failed', message: current.error || 'Analysis failed' }
    await refresh()
    setCurrentId(project.id)
    return current
  }, [refresh, setCurrentId])

  const start = useCallback(async (kind, payload) => {
    cancelled.current = false
    setState({ busy: true, run: null, error: null, project: null, upload: 0 })
    try {
      let res
      if (kind === 'sample') res = await api.loadSample()
      else if (kind === 'zip') res = await api.uploadZip(payload, (p) => setState((s) => ({ ...s, upload: p })))
      else if (kind === 'github') res = await api.loadGithub(payload)
      else if (kind === 'local') res = await api.loadLocal(payload)
      else if (kind === 'reanalyse') res = { project: payload, run: await api.analyze(payload.id) }
      const run = await follow(res.project, res.run)
      setState((s) => ({ ...s, busy: false, run }))
      return { project: res.project, run }
    } catch (error) {
      setState((s) => ({ ...s, busy: false, error }))
      return null
    }
  }, [follow])

  const reset = () => {
    cancelled.current = true
    setState({ busy: false, run: null, error: null, project: null, upload: 0 })
  }

  return { ...state, start, reset }
}
