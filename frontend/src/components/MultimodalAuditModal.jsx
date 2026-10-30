import { useState, useRef, useEffect } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import { Camera, UploadCloud, Sparkles, CheckCircle2, AlertTriangle, X, RefreshCw, Layers, ShieldAlert, Cpu } from 'lucide-react'
import { Button, Modal, Badge, Card } from './ui'
import { api } from '../services/api'

export default function MultimodalAuditModal({ open, onClose, projectId }) {
  const maxFileSize = 10 * 1024 * 1024
  const [file, setFile] = useState(null)
  const [previewUrl, setPreviewUrl] = useState(null)
  const [diagramType, setDiagramType] = useState('architecture_diagram')
  const [notes, setNotes] = useState('')
  const [consent, setConsent] = useState(false)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)
  const [result, setResult] = useState(null)
  const fileInputRef = useRef(null)
  const previewUrlRef = useRef(null)

  useEffect(() => () => {
    if (previewUrlRef.current) URL.revokeObjectURL(previewUrlRef.current)
  }, [])

  const handleFileChange = (e) => {
    const selected = e.target.files?.[0]
    if (selected) {
      if (selected.size > maxFileSize) {
        if (previewUrlRef.current) URL.revokeObjectURL(previewUrlRef.current)
        previewUrlRef.current = null
        setFile(null)
        setPreviewUrl(null)
        setConsent(false)
        setError('Image must be 10MB or smaller.')
        return
      }
      if (!selected.type.startsWith('image/')) {
        if (previewUrlRef.current) URL.revokeObjectURL(previewUrlRef.current)
        previewUrlRef.current = null
        setFile(null)
        setPreviewUrl(null)
        setConsent(false)
        setError('Please select an image file (PNG, JPG, WebP).')
        return
      }
      if (previewUrlRef.current) URL.revokeObjectURL(previewUrlRef.current)
      setFile(selected)
      setConsent(false)
      setError(null)
      const url = URL.createObjectURL(selected)
      previewUrlRef.current = url
      setPreviewUrl(url)
    }
  }

  const handleAudit = async () => {
    if (!file || !consent) return
    setLoading(true)
    setError(null)
    try {
      const res = await api.multimodalAudit(projectId, file, diagramType, notes)
      setResult(res.result)
    } catch (err) {
      setError(err?.message || 'Multimodal visual audit failed. Ensure Gemma API key is configured.')
    } finally {
      setLoading(false)
    }
  }

  const handleReset = () => {
    if (previewUrlRef.current) URL.revokeObjectURL(previewUrlRef.current)
    previewUrlRef.current = null
    setFile(null)
    setPreviewUrl(null)
    setConsent(false)
    setResult(null)
    setError(null)
    setNotes('')
    if (fileInputRef.current) fileInputRef.current.value = ''
  }

  return (
    <Modal open={open} onClose={onClose} title="Gemma 4 Multimodal Visual Architecture Audit" size="xl">
      <div className="space-y-4">
        <p className="text-sm text-ink-300">
          Upload an architectural diagram, cloud schema, or runtime error screenshot. DevDNA uses{' '}
          <strong className="text-violet-300 font-medium">Gemma 4 multimodal intelligence</strong> to visually inspect the
          image and compare it directly against the ground-truth AST code graph to detect architectural drift and unmitigated failure points.
        </p>

        {!result ? (
          <div className="space-y-4">
            <div
              onClick={() => fileInputRef.current?.click()}
              className="cursor-pointer border-2 border-dashed border-ink-600 hover:border-violet-400/60 rounded-xl p-6 text-center transition-colors bg-ink-900/40 hover:bg-ink-900/70"
            >
              <input
                ref={fileInputRef}
                type="file"
                accept="image/png,image/jpeg,image/webp"
                className="hidden"
                onChange={handleFileChange}
              />
              {previewUrl ? (
                <div className="space-y-3">
                  <img
                    src={previewUrl}
                    alt="Uploaded diagram preview"
                    className="max-h-56 mx-auto rounded-lg object-contain shadow-lg border border-white/10"
                  />
                  <p className="text-xs text-ink-400 font-mono">{file.name} ({(file.size / 1024).toFixed(1)} KB)</p>
                  <Button variant="ghost" size="sm" onClick={(e) => { e.stopPropagation(); handleReset(); }}>
                    Choose Different Image
                  </Button>
                </div>
              ) : (
                <div className="space-y-2">
                  <div className="inline-flex h-12 w-12 items-center justify-center rounded-full bg-violet-500/10 text-violet-400">
                    <UploadCloud className="h-6 w-6" />
                  </div>
                  <div className="text-sm font-medium text-ink-100">
                    Drop architecture diagram or click to browse
                  </div>
                  <p className="text-xs text-ink-400">PNG, JPG, or WebP up to 10MB</p>
                </div>
              )}
            </div>

            <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
              <div>
                <label className="block text-xs font-medium text-ink-300 mb-1">Image / Diagram Type</label>
                <select
                  value={diagramType}
                  onChange={(e) => { setDiagramType(e.target.value); setConsent(false) }}
                  className="w-full h-9 rounded-lg border border-ink-600 bg-ink-900 px-3 text-sm text-ink-100 outline-none focus:border-violet-400"
                >
                  <option value="architecture_diagram">System Architecture / C4 Diagram</option>
                  <option value="cloud_infrastructure">Cloud Infrastructure Topology (AWS/GCP/K8s)</option>
                  <option value="whiteboard_sketch">Whiteboard Architecture Sketch</option>
                  <option value="error_screenshot">Runtime Error / Trace Screenshot</option>
                </select>
              </div>
              <div>
                <label className="block text-xs font-medium text-ink-300 mb-1">Audit Focus / Notes (Optional)</label>
                <input
                  type="text"
                  value={notes}
                  onChange={(e) => { setNotes(e.target.value); setConsent(false) }}
                  maxLength={2000}
                  placeholder="e.g. Check database failover and checkout API"
                  className="w-full h-9 rounded-lg border border-ink-600 bg-ink-900 px-3 text-sm text-ink-100 outline-none focus:border-violet-400"
                />
              </div>
            </div>

            <label className="flex items-start gap-2 text-sm text-ink-300">
              <input
                type="checkbox"
                checked={consent}
                onChange={(e) => setConsent(e.target.checked)}
                className="mt-0.5 accent-violet-500"
              />
              <span>I understand this diagram and notes will be sent to Gemma</span>
            </label>

            {error && (
              <div className="p-3 rounded-lg bg-rose-500/10 border border-rose-500/20 text-rose-300 text-sm flex items-start gap-2">
                <AlertTriangle className="h-4 w-4 shrink-0 mt-0.5" />
                <span>{error}</span>
              </div>
            )}

            <div className="flex justify-end gap-2 pt-2 border-t border-white/[0.06]">
              <Button variant="ghost" onClick={onClose} disabled={loading}>
                Cancel
              </Button>
              <Button
                variant="primary"
                onClick={handleAudit}
                disabled={!file || !consent || loading}
                className="gap-2"
              >
                {loading ? (
                  <>
                    <RefreshCw className="h-4 w-4 animate-spin" />
                    Visually Auditing with Gemma 4...
                  </>
                ) : (
                  <>
                    <Sparkles className="h-4 w-4" />
                    Analyze Diagram with Gemma 4
                  </>
                )}
              </Button>
            </div>
          </div>
        ) : (
          <div className="space-y-4 max-h-[70vh] overflow-y-auto pr-1">
            <div className="p-4 rounded-xl bg-violet-950/20 border border-violet-500/30 space-y-2">
              <div className="flex items-center justify-between">
                <span className="text-xs uppercase tracking-wider font-semibold text-violet-300 flex items-center gap-1.5">
                  <Sparkles className="h-3.5 w-3.5" /> Gemma 4 Visual Synthesis
                </span>
                <Badge variant={result.confidence === 'high' ? 'success' : 'warning'}>
                  {result.confidence} confidence
                </Badge>
              </div>
              <p className="text-sm text-ink-100">{result.summary}</p>
            </div>

            <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
              {/* Detected Visual Components */}
              <Card className="p-3.5 space-y-2">
                <div className="flex items-center gap-1.5 text-xs font-semibold uppercase text-ink-300">
                  <Layers className="h-3.5 w-3.5 text-cyan-400" /> Detected Diagram Nodes
                </div>
                <div className="flex flex-wrap gap-1.5">
                  {result.detected_components?.length > 0 ? (
                    result.detected_components.map((c, i) => (
                      <span key={i} className="px-2 py-0.5 rounded text-xs bg-cyan-950/40 text-cyan-300 border border-cyan-800/40">
                        {c}
                      </span>
                    ))
                  ) : (
                    <span className="text-xs text-ink-400">None detected</span>
                  )}
                </div>
              </Card>

              {/* Code Alignment */}
              <Card className="p-3.5 space-y-2">
                <div className="flex items-center gap-1.5 text-xs font-semibold uppercase text-ink-300">
                  <CheckCircle2 className="h-3.5 w-3.5 text-emerald-400" /> Code AST Alignment
                </div>
                <ul className="text-xs space-y-1 text-ink-200">
                  {result.code_alignment?.map((item, i) => (
                    <li key={i} className="flex items-start gap-1.5">
                      <span className="text-emerald-400 mt-0.5">•</span>
                      <span>{item}</span>
                    </li>
                  ))}
                </ul>
              </Card>
            </div>

            {/* Architectural Drift */}
            {result.architectural_drift?.length > 0 && (
              <Card className="p-3.5 space-y-2 border-amber-500/20 bg-amber-950/10">
                <div className="flex items-center gap-1.5 text-xs font-semibold uppercase text-amber-300">
                  <AlertTriangle className="h-3.5 w-3.5" /> Architectural Drift (Diagram vs Code)
                </div>
                <div className="space-y-1.5">
                  {result.architectural_drift.map((drift, i) => (
                    <div key={i} className="flex items-start justify-between gap-2 p-2 rounded bg-ink-900/50 border border-white/5 text-xs">
                      <div>
                        <strong className="text-ink-100 font-mono">{drift.component}</strong>: {drift.issue}
                      </div>
                      <Badge variant={drift.severity === 'high' ? 'danger' : 'warning'}>
                        {drift.severity}
                      </Badge>
                    </div>
                  ))}
                </div>
              </Card>
            )}

            {/* Failure Susceptibility */}
            {result.failure_susceptibility?.length > 0 && (
              <Card className="p-3.5 space-y-2 border-rose-500/20 bg-rose-950/10">
                <div className="flex items-center gap-1.5 text-xs font-semibold uppercase text-rose-300">
                  <ShieldAlert className="h-3.5 w-3.5" /> Single Points of Failure Identified
                </div>
                <ul className="text-xs space-y-1 text-ink-200">
                  {result.failure_susceptibility.map((item, i) => (
                    <li key={i} className="flex items-start gap-1.5">
                      <span className="text-rose-400 mt-0.5">•</span>
                      <span>{item}</span>
                    </li>
                  ))}
                </ul>
              </Card>
            )}

            {/* Recommendations */}
            {result.recommendations?.length > 0 && (
              <Card className="p-3.5 space-y-2">
                <div className="flex items-center gap-1.5 text-xs font-semibold uppercase text-ink-300">
                  <Cpu className="h-3.5 w-3.5 text-violet-400" /> Recommendations to Resiliently Align Code
                </div>
                <div className="space-y-1.5">
                  {result.recommendations.map((rec, i) => (
                    <div key={i} className="text-xs p-2 rounded bg-ink-900/40 border border-white/5">
                      <div className="font-semibold text-ink-100">{rec.title}</div>
                      <div className="text-ink-300 mt-0.5">{rec.detail}</div>
                    </div>
                  ))}
                </div>
              </Card>
            )}

            <div className="flex justify-between items-center pt-3 border-t border-white/[0.06]">
              <Button variant="ghost" size="sm" onClick={handleReset}>
                Audit Another Diagram
              </Button>
              <Button variant="primary" size="sm" onClick={onClose}>
                Done
              </Button>
            </div>
          </div>
        )}
      </div>
    </Modal>
  )
}
