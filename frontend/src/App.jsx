import { MotionConfig } from 'framer-motion'
import { BrowserRouter, Route, Routes } from 'react-router-dom'
import { GemmaProvider } from './hooks/useGemma'
import { ProjectProvider } from './hooks/useProject'
import { SettingsProvider, useSettings } from './hooks/useSettings'
import { StatusProvider } from './hooks/useStatus'
import ErrorBoundary from './components/ErrorBoundary'
import AppLayout from './layouts/AppLayout'
import Architecture from './pages/Architecture'
import ExperimentDetail from './pages/ExperimentDetail'
import Experiments from './pages/Experiments'
import Findings from './pages/Findings'
import NotFound from './pages/NotFound'
import Overview from './pages/Overview'
import Prediction from './pages/Prediction'
import Reports from './pages/Reports'
import Settings from './pages/Settings'
import WhatIfLab from './pages/WhatIfLab'

function Motion({ children }) {
  const { settings } = useSettings()
  const reducedMotion = settings.motion === 'reduced' ? 'always' : settings.motion === 'full' ? 'never' : 'user'
  return <MotionConfig reducedMotion={reducedMotion}>{children}</MotionConfig>
}

export default function App() {
  return (
    <SettingsProvider>
      <Motion>
        <StatusProvider>
          <ProjectProvider>
            <GemmaProvider>
              <BrowserRouter future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
                <ErrorBoundary>
                  <Routes>
                    <Route element={<AppLayout />}>
                      <Route index element={<Overview />} />
                      <Route path="architecture" element={<Architecture />} />
                      <Route path="prediction" element={<Prediction />} />
                      <Route path="lab" element={<WhatIfLab />} />
                      <Route path="findings" element={<Findings />} />
                      <Route path="experiments" element={<Experiments />} />
                      <Route path="experiments/:id" element={<ExperimentDetail />} />
                      <Route path="reports" element={<Reports />} />
                      <Route path="settings" element={<Settings />} />
                      <Route path="*" element={<NotFound />} />
                    </Route>
                  </Routes>
                </ErrorBoundary>
              </BrowserRouter>
            </GemmaProvider>
          </ProjectProvider>
        </StatusProvider>
      </Motion>
    </SettingsProvider>
  )
}
