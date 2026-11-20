import { createContext, useCallback, useContext, useEffect, useMemo, useState } from 'react'
import { api } from '../services/api'
import { readStore, writeStore } from './storage'

const ProjectContext = createContext(null)

/** Holds the project list and the currently selected project for the whole app. */
export function ProjectProvider({ children }) {
  const [projects, setProjects] = useState([])
  const [currentId, setCurrentIdState] = useState(() => readStore('devdna.project', null))
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)

  const refresh = useCallback(async () => {
    try {
      const list = await api.listProjects()
      setProjects(list)
      setError(null)
      setCurrentIdState((id) => (id && list.some((p) => p.id === id) ? id : list[0]?.id || null))
      return list
    } catch (e) {
      setError(e)
      return []
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    refresh()
  }, [refresh])

  useEffect(() => {
    writeStore('devdna.project', currentId)
  }, [currentId])

  const value = useMemo(() => {
    const current = projects.find((p) => p.id === currentId) || null
    return {
      projects,
      current,
      project: current,
      currentId,
      loading,
      error,
      refresh,
      setCurrentId: setCurrentIdState,
      ready: Boolean(current?.latest_run_id),
    }
  }, [projects, currentId, loading, error, refresh])

  return <ProjectContext.Provider value={value}>{children}</ProjectContext.Provider>
}

export function useProject() {
  const ctx = useContext(ProjectContext)
  if (!ctx) throw new Error('useProject must be used inside ProjectProvider')
  return ctx
}
