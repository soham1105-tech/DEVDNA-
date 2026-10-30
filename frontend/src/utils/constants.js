import {
  AppWindow, Boxes, Braces, Cpu, Database, FileCode2, FlaskConical, Globe, KeyRound, Route,
} from 'lucide-react'

/**
 * Visual identity for each component type returned by the analyzer.
 * Four chromatic hues plus neutral greys: blue for routes and code structure,
 * amber for databases, red for external APIs, green for env vars and tests.
 */
export const NODE_TYPES = {
  route: { label: 'HTTP route', color: '#4a8ad6', icon: Route },
  app: { label: 'Application', color: '#b4b7bd', icon: AppWindow },
  function: { label: 'Function', color: '#8f9196', icon: Braces },
  class: { label: 'Class', color: '#a0a2a8', icon: Boxes },
  module: { label: 'Module', color: '#6f7178', icon: FileCode2 },
  database: { label: 'Database', color: '#d9a441', icon: Database },
  external_api: { label: 'External API', color: '#e5566f', icon: Globe },
  env_var: { label: 'Env variable', color: '#44a282', icon: KeyRound },
  worker: { label: 'Background worker', color: '#2f6aa8', icon: Cpu },
  test: { label: 'Test', color: '#7fb5a0', icon: FlaskConical },
}

export const EDGE_LABELS = {
  calls: 'calls',
  handled_by: 'handled by',
  served_by: 'served by',
  runs_on_startup: 'runs on startup',
  uses_database: 'uses database',
  calls_http: 'calls HTTP',
  reads_env: 'reads env',
  exercises: 'exercises',
  contains: 'contains',
  imports: 'imports',
  runs_worker: 'runs worker',
}

export const PROPAGATING = ['calls', 'handled_by', 'served_by', 'runs_on_startup', 'uses_database', 'calls_http', 'reads_env', 'exercises']

export const SEVERITY = {
  high: { label: 'High', cls: 'bg-negative-bg text-negative-ink' },
  medium: { label: 'Medium', cls: 'bg-warning-bg text-warning-ink' },
  low: { label: 'Low', cls: 'bg-neutral-bg text-neutral-ink' },
  info: { label: 'Info', cls: 'bg-neutral-bg text-neutral-ink' },
}

export const EXPERIMENT_STATUS = {
  prediction_only: { label: 'Prediction only', cls: 'bg-neutral-bg text-neutral-ink' },
  queued: { label: 'Queued', cls: 'bg-neutral-bg text-neutral-ink' },
  running: { label: 'Running', cls: 'bg-neutral-bg text-neutral-ink' },
  experiment_executed: { label: 'Experiment executed', cls: 'bg-neutral-bg text-neutral-ink' },
  prediction_confirmed: { label: 'Prediction confirmed', cls: 'bg-positive-bg text-positive-ink' },
  partially_confirmed: { label: 'Partially confirmed', cls: 'bg-warning-bg text-warning-ink' },
  prediction_contradicted: { label: 'Prediction contradicted', cls: 'bg-negative-bg text-negative-ink' },
  inconclusive: { label: 'Inconclusive', cls: 'bg-neutral-bg text-neutral-ink' },
  blocked: { label: 'Blocked', cls: 'bg-warning-bg text-warning-ink' },
  failed: { label: 'Execution failed', cls: 'bg-negative-bg text-negative-ink' },
}

export const SCENARIO_ICONS = { db_unavailable: Database, external_api_unavailable: Globe, missing_env_var: KeyRound, worker_unavailable: Cpu }
