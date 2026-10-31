import axios from 'axios'

// In development Vite proxies /api to the backend (see vite.config.js).
// Set VITE_API_URL to call a backend on another origin.
const http = axios.create({ baseURL: import.meta.env.VITE_API_URL || '', timeout: 120000 })

// Opt-in API auth: when the backend requires a token (DEVDNA_API_TOKEN), the
// user sets it in Settings. Read from sessionStorage on every call so a
// change applies immediately; never persisted to localStorage.
const TOKEN_KEY = 'devdna.apiToken'
http.interceptors.request.use((config) => {
  const token = sessionStorage.getItem(TOKEN_KEY)
  if (token) config.headers['X-DevDNA-Token'] = token
  return config
})

export function setApiToken(token) {
  if (token) sessionStorage.setItem(TOKEN_KEY, token)
  else sessionStorage.removeItem(TOKEN_KEY)
}

export function hasApiToken() {
  return Boolean(sessionStorage.getItem(TOKEN_KEY))
}

/** Turn any axios error into { status, code, message, fields }. */
export function toApiError(err) {
  if (err?.response) {
    const e = err.response.data?.error || {}
    return {
      status: err.response.status,
      code: e.code || 'http_error',
      message: e.message || `Request failed (${err.response.status})`,
      fields: e.fields || [],
    }
  }
  if (err?.code === 'ECONNABORTED') return { status: 0, code: 'timeout', message: 'The request timed out.' }
  return { status: 0, code: 'network', message: 'Cannot reach the DevDNA backend. Is it running on port 8000?' }
}

async function call(method, url, data, config) {
  try {
    const res = await http.request({ method, url, data, ...config })
    return res.data
  } catch (err) {
    throw toApiError(err)
  }
}

const enc = encodeURIComponent
// Gemma reasons before answering; streamed responses can take 1-3 minutes.
const AI = { timeout: 240000 }

export const api = {
  health: () => call('get', '/api/health'),
  aiStatus: (check = false) => call('get', `/api/ai/status${check ? '?check=true' : ''}`),

  listProjects: () => call('get', '/api/projects'),
  getProject: (id) => call('get', `/api/projects/${id}`),
  loadSample: () => call('post', '/api/projects/sample'),
  loadGithub: (payload) => call('post', '/api/projects/github', payload),
  uploadZip: ({ file, githubUrl, githubBranch }, onProgress) => {
    const form = new FormData()
    form.append('file', file)
    if (githubUrl?.trim()) {
      form.append('github_url', githubUrl.trim())
      if (githubBranch?.trim()) form.append('github_branch', githubBranch.trim())
    }
    return call('post', '/api/projects/upload', form, {
      onUploadProgress: (e) => onProgress?.(e.total ? e.loaded / e.total : 0),
    })
  },
  loadLocal: (payload) => call('post', '/api/projects/local', typeof payload === 'string' ? { path: payload } : payload),
  deleteProject: (id) => call('delete', `/api/projects/${id}`),
  analyze: (id) => call('post', `/api/projects/${id}/analyze`),
  getRun: (runId) => call('get', `/api/runs/${runId}`),

  architecture: (id) => call('get', `/api/projects/${id}/architecture`),
  component: (id, componentId) => call('get', `/api/projects/${id}/components/${componentId.split('/').map(enc).join('/')}`),
  findings: (id) => call('get', `/api/projects/${id}/findings`),
  updateFinding: (findingId, review_status) => call('patch', `/api/findings/${findingId}`, { review_status }),
  suppressFinding: (findingId, payload) => call('post', `/api/findings/${findingId}/suppress`, payload),
  unsuppressFinding: (findingId) => call('post', `/api/findings/${findingId}/unsuppress`),
  findingSuppressionAudit: (findingId) => call('get', `/api/findings/${findingId}/suppression-audit`),
  drift: (id) => call('get', `/api/projects/${id}/drift`),
  scenarios: (id) => call('get', `/api/projects/${id}/scenarios`),
  analytics: (id) => call('get', `/api/projects/${id}/analytics`),

  createPrediction: (id, scenario, target_id, custom_spec = null) =>
    call('post', `/api/projects/${id}/predictions`, { scenario, target_id, custom_spec }),
  predictions: (id) => call('get', `/api/projects/${id}/predictions`),
  prediction: (predictionId) => call('get', `/api/predictions/${predictionId}`),
  startExperiment: (predictionId) => call('post', `/api/predictions/${predictionId}/experiments`, { approved: true }),
  experiments: (id) => call('get', `/api/projects/${id}/experiments`),
  experiment: (experimentId) => call('get', `/api/experiments/${experimentId}`),

  // AI: every endpoint accepts { preview } (returns the evidence package) or { consent: true } (calls Gemma)
  explainArchitecture: (id, body) => call('post', `/api/projects/${id}/ai/architecture`, body, AI),
  latestArchitecture: (id) => call('get', `/api/projects/${id}/ai/architecture/latest`),
  explainPrediction: (predictionId, body) => call('post', `/api/predictions/${predictionId}/explain`, body, AI),
  interpretExperiment: (experimentId, body) => call('post', `/api/experiments/${experimentId}/interpret`, body, AI),
  chat: (id, body) => call('post', `/api/projects/${id}/ai/chat`, body, AI),
  aiInteractions: (id) => call('get', `/api/projects/${id}/ai/interactions`),

  // Gemma 4 Multimodal & Dynamic Failure Synthesis
  multimodalAudit: (id, file, diagram_type = 'architecture_diagram', notes = '') => {
    const form = new FormData()
    form.append('file', file)
    form.append('diagram_type', diagram_type)
    form.append('notes', notes)
    form.append('consent', 'true')
    return call('post', `/api/projects/${id}/ai/multimodal-audit`, form, AI)
  },
  latestMultimodalAudit: (id) => call('get', `/api/projects/${id}/ai/multimodal-audit/latest`),
  dynamicScenario: (id, body) => call('post', `/api/projects/${id}/ai/dynamic-scenario`, body, AI),

  reportHistory: (id) => call('get', `/api/projects/${id}/reports`),
}

/** Download a report file through the API and save it in the browser. */
export async function downloadFile(url, fallbackName) {
  try {
    const res = await http.get(url, { responseType: 'blob' })
    const disposition = res.headers['content-disposition'] || ''
    const match = disposition.match(/filename="([^"]+)"/)
    const name = match ? match[1] : fallbackName
    const href = URL.createObjectURL(res.data)
    const a = document.createElement('a')
    a.href = href
    a.download = name
    document.body.appendChild(a)
    a.click()
    a.remove()
    setTimeout(() => URL.revokeObjectURL(href), 1000)
    return name
  } catch (err) {
    throw toApiError(err)
  }
}
