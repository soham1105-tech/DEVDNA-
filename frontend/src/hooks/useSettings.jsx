import { createContext, useContext, useEffect, useMemo, useState } from 'react'
import { readStore, writeStore } from './storage'

const SettingsContext = createContext(null)

const DEFAULTS = { motion: 'system', consentMode: 'ask', theme: 'dark', learningMode: 'technical' }

/** Per-browser UI preferences: motion level, theme (dark/bright), and AI consent behaviour. */
export function SettingsProvider({ children }) {
  const [settings, setSettings] = useState(() => ({ ...DEFAULTS, ...readStore('devdna.settings', {}) }))

  useEffect(() => {
    writeStore('devdna.settings', settings)
    document.documentElement.dataset.motion = settings.motion === 'reduced' ? 'reduced' : 'normal'
    const theme = settings.theme || 'dark'
    document.documentElement.dataset.theme = theme
    if (theme === 'bright') {
      document.documentElement.classList.add('bright')
    } else {
      document.documentElement.classList.remove('bright')
    }
  }, [settings])

  const toggleTheme = () => {
    setSettings((s) => ({ ...s, theme: s.theme === 'bright' ? 'dark' : 'bright' }))
  }

  const value = useMemo(
    () => ({ settings, update: (patch) => setSettings((s) => ({ ...s, ...patch })), toggleTheme }),
    [settings],
  )
  return <SettingsContext.Provider value={value}>{children}</SettingsContext.Provider>
}

export function useSettings() {
  return useContext(SettingsContext)
}
