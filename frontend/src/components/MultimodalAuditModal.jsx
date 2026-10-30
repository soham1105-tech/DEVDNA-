import { useState, useRef, useEffect } from 'react'
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
    <Modal open={open} onClose={onClose} title="Gemma 4 Multimodal Visual Architecture Audit" width="max-w-3xl">
      <div className="space-y-4">
        <p className="text-sm text-muted">
          Upload an architectural diagram, cloud schema, or runtime error screenshot. DevDNA uses{' '}
          <strong className="font-medium text-ink">Gemma 4 multimodal intelligence</strong> to visually inspect the
          image and compare it directly against the ground-truth AST code graph to detect architectural drift and unmitigated failure points.
        </p>

        {!result ? (
          <div className="space-y-4">
            <div
              onClick={() => fileInputRef.current?.click()}
              className="cursor-pointer rounded-card border border-dashed border-control bg-ground p-6 text-center transition-colors hover:border-accent"
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
                    className="mx-auto max-h-56 rounded-card border border-edge object-contain"
                  />
                  <p className="num text-xs text-faint">{file.name} ({(file.size / 1024).toFixed(1)} KB)</p>
                  <Button variant="ghost" size="sm" onClick={(e) => { e.stopPropagation(); handleReset(); }}>
                    Choose Different Image
                  </Button>
                </div>
              ) : (
                <div className="space-y-2">
                  <UploadCloud className="mx-auto h-6 w-6 text-faint" />
                  <div className="text-sm font-medium text-ink">
                    Drop architecture diagram or click to browse
                  </div>
                  <p className="text-xs text-faint">PNG, JPG, or WebP up to 10MB</p>
                </div>
              )}
            </div>

            <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
              <div>
                <label className="mb-1 block text-xs font-medium text-muted" htmlFor="diagram-type">Image / Diagram Type</label>
                <select
                  id="diagram-type"
                  value={diagramType}
                  onChange={(e) => { setDiagramType(e.target.value); setConsent(false) }}
                  className="h-9 w-full rounded-control border border-control bg-surface px-3 text-sm text-ink outline-none focus:border-accent"
                >
                  <option value="architecture_diagram">System Architecture / C4 Diagram</option>
                  <option value="cloud_infrastructure">Cloud Infrastructure Topology (AWS/GCP/K8s)</option>
                  <option value="whiteboard_sketch">Whiteboard Architecture Sketch</option>
                  <option value="error_screenshot">Runtime Error / Trace Screenshot</option>
                </select>
              </div>
              <div>
                <label className="mb-1 block text-xs font-medium text-muted" htmlFor="audit-notes">Audit Focus / Notes (Optional)</label>
                <input
                  id="audit-notes"
                  type="text"
                  value={notes}
                  onChange={(e) => { setNotes(e.target.value); setConsent(false) }}
                  maxLength={2000}
                  placeholder="e.g. Check database failover and checkout API"
                  className="h-9 w-full rounded-control border border-control bg-surface px-3 text-sm text-ink outline-none focus:border-accent"
                />
              </div>
            </div>

            <label className="flex items-start gap-2 text-sm text-muted">
              <input
                type="checkbox"
                checked={consent}
                onChange={(e) => setConsent(e.target.checked)}
                className="mt-0.5 accent-[var(--color-accent)]"
              />
              <span>I understand this diagram and notes will be sent to Gemma</span>
            </label>

            {error && (
              <div className="flex items-start gap-2 rounded-card border border-negative/25 bg-negative-bg p-3 text-sm text-negative-ink">
                <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
                <span>{error}</span>
              </div>
            )}

            <div className="flex justify-end gap-2 border-t border-rule pt-2">
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
          <div className="max-h-[70vh] space-y-4 overflow-y-auto pr-1">
            <div className="space-y-2 rounded-card border border-accent/30 bg-accent/10 p-4">
              <div className="flex items-center justify-between">
                <span className="flex items-center gap-1.5 text-xs font-medium text-accent">
                  <Sparkles className="h-3.5 w-3.5" /> Gemma 4 Visual Synthesis
                </span>
                <Badge className={result.confidence === 'high' ? 'bg-positive-bg text-positive-ink' : 'bg-warning-bg text-warning-ink'}>
                  {result.confidence} confidence
                </Badge>
              </div>
              <p className="text-sm text-ink">{result.summary}</p>
            </div>

            <div className="grid grid-cols-1 items-start gap-3 md:grid-cols-2">
              {/* Detected Visual Components */}
              <Card className="space-y-2 p-3.5">
                <div className="flex items-center gap-1.5 text-xs font-medium text-muted">
                  <Layers className="h-3.5 w-3.5 text-accent" /> Detected Diagram Nodes
                </div>
                <div className="flex flex-wrap gap-1.5">
                  {result.detected_components?.length > 0 ? (
                    result.detected_components.map((c, i) => (
                      <span key={i} className="rounded-full bg-accent/10 px-2 py-0.5 text-xs text-accent">
                        {c}
                      </span>
                    ))
                  ) : (
                    <span className="text-xs text-faint">None detected</span>
                  )}
                </div>
              </Card>

              {/* Code Alignment */}
              <Card className="space-y-2 p-3.5">
                <div className="flex items-center gap-1.5 text-xs font-medium text-muted">
                  <CheckCircle2 className="h-3.5 w-3.5 text-positive" /> Code AST Alignment
                </div>
                <ul className="space-y-1 text-xs text-muted">
                  {result.code_alignment?.map((item, i) => (
                    <li key={i} className="flex items-start gap-1.5">
                      <span className="mt-0.5 text-positive">•</span>
                      <span>{item}</span>
                    </li>
                  ))}
                </ul>
              </Card>
            </div>

            {/* Architectural Drift */}
            {result.architectural_drift?.length > 0 && (
              <Card className="space-y-2 border-warning/25 p-3.5">
                <div className="flex items-center gap-1.5 text-xs font-medium text-warning">
                  <AlertTriangle className="h-3.5 w-3.5" /> Architectural Drift (Diagram vs Code)
                </div>
                <div className="space-y-1.5">
                  {result.architectural_drift.map((drift, i) => (
                    <div key={i} className="flex items-start justify-between gap-2 rounded-card bg-ground p-2 text-xs">
                      <div className="text-muted">
                        <strong className="num text-ink">{drift.component}</strong>: {drift.issue}
                      </div>
                      <Badge className={drift.severity === 'high' ? 'bg-negative-bg text-negative-ink' : 'bg-warning-bg text-warning-ink'}>
                        {drift.severity}
                      </Badge>
                    </div>
                  ))}
                </div>
              </Card>
            )}

            {/* Failure Susceptibility */}
            {result.failure_susceptibility?.length > 0 && (
              <Card className="space-y-2 border-negative/25 p-3.5">
                <div className="flex items-center gap-1.5 text-xs font-medium text-negative">
                  <ShieldAlert className="h-3.5 w-3.5" /> Single Points of Failure Identified
                </div>
                <ul className="space-y-1 text-xs text-muted">
                  {result.failure_susceptibility.map((item, i) => (
                    <li key={i} className="flex items-start gap-1.5">
                      <span className="mt-0.5 text-negative">•</span>
                      <span>{item}</span>
                    </li>
                  ))}
                </ul>
              </Card>
            )}

            {/* Recommendations */}
            {result.recommendations?.length > 0 && (
              <Card className="space-y-2 p-3.5">
                <div className="flex items-center gap-1.5 text-xs font-medium text-muted">
                  <Cpu className="h-3.5 w-3.5 text-accent" /> Recommendations to Resiliently Align Code
                </div>
                <div className="space-y-1.5">
                  {result.recommendations.map((rec, i) => (
                    <div key={i} className="rounded-card bg-ground p-2 text-xs">
                      <div className="font-semibold text-ink">{rec.title}</div>
                      <div className="mt-0.5 text-muted">{rec.detail}</div>
                    </div>
                  ))}
                </div>
              </Card>
            )}

            <div className="flex items-center justify-between border-t border-rule pt-3">
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
