import { List, Refs } from './AIPanel'
import { Badge } from './ui'

const CONF = { high: 'bg-positive-bg text-positive-ink', medium: 'bg-warning-bg text-warning-ink', low: 'bg-negative-bg text-negative-ink' }

export function ArchitectureResult({ r }) {
  return (
    <div>
      <p className="text-sm leading-relaxed text-muted">{r.summary}</p>
      {r.layers?.length > 0 && (
        <div className="mt-4 grid items-start gap-2 sm:grid-cols-2">
          {r.layers.map((l, i) => (
            <div key={i} className="rounded-card border border-edge bg-ground p-3">
              <div className="text-sm font-medium text-ink">{l.name}</div>
              <div className="mt-0.5 text-xs text-muted">{l.role}</div>
              <div className="mt-2 flex flex-wrap gap-1">
                {l.components.slice(0, 6).map((c) => (
                  <span key={c} className="num rounded bg-raised px-1.5 py-0.5 text-[11px] text-muted">{c.replace(/^\w+:/, '')}</span>
                ))}
              </div>
            </div>
          ))}
        </div>
      )}
      {r.key_flows?.map((f, i) => (
        <div key={i} className="mt-3">
          <div className="text-xs font-medium text-ink">{f.name}</div>
          <ol className="mt-1 space-y-0.5 text-sm text-muted">
            {f.steps.map((s, j) => (
              <li key={j} className="flex gap-2">
                <span className="num text-xs text-faint">{j + 1}.</span>
                {s}
              </li>
            ))}
          </ol>
        </div>
      ))}
      <List title="Risks" items={r.risks} />
      <List title="Unknowns (not determinable statically)" items={r.unknowns} tone="text-faint" />
      <Refs refs={r.evidence_used} />
    </div>
  )
}

export function FailureAnalysisResult({ r }) {
  return (
    <div>
      <div className="flex items-start justify-between gap-3">
        <p className="text-sm leading-relaxed text-muted">{r.summary}</p>
        <Badge className={CONF[r.confidence]}>confidence: {r.confidence}</Badge>
      </div>
      <div className="mt-3 rounded-card border border-edge bg-ground p-3 text-sm text-muted">
        <div className="mb-1 text-xs font-medium text-ink">Failure mechanism</div>
        {r.failure_mechanism}
      </div>
      {r.user_impact && <p className="mt-3 text-sm text-muted"><span className="font-medium text-ink">User impact:</span> {r.user_impact}</p>}
      <List title="Predicted symptoms" items={r.predicted_symptoms} />
      <List title="Assumptions" items={r.assumptions} tone="text-faint" />
      <List title="Uncertainties" items={r.uncertainties} tone="text-warning" />
      <List title="Recommended tests" items={r.recommended_tests} />
      <List title="Mitigations" items={r.mitigations} />
      <Refs refs={r.evidence_used} />
    </div>
  )
}

export function InterpretationResult({ r }) {
  return (
    <div>
      <p className="text-sm leading-relaxed text-muted">{r.verdict_explanation}</p>
      <List title="Agreements" items={r.agreements} />
      <List title="Discrepancies" items={r.discrepancies} tone="text-warning" />
      <List title="Lessons" items={r.lessons} />
      {r.next_test && (
        <div className="mt-3 rounded-card border border-accent/30 bg-accent/10 p-3 text-sm text-ink">
          <span className="font-medium">Next test: </span>
          {r.next_test}
        </div>
      )}
      <List title="Caveats" items={r.caveats} tone="text-faint" />
      <Refs refs={r.evidence_used} />
    </div>
  )
}
