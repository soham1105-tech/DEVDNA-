/**
 * Deterministic "Explain This Analysis" builder.
 *
 * Pure read-only function: never mutates input analysis data, never invents
 * project-specific evidence, and strictly distinguishes static predictions
 * from executed experiments (never claiming blocked/unexecuted runs succeeded).
 */

export function buildAnalysisExplanation(kind, data, mode = 'technical') {
  const beginner = mode === 'beginner'

  if (!data) {
    return {
      title: 'No analysis selected yet',
      badge: 'Requires data',
      whatCalculated: 'No analysis record is currently loaded for this view.',
      simpleMeaning: beginner
        ? 'Run or select an analysis first so DevDNA has real project evidence to explain.'
        : 'Evidence is unavailable because no completed analysis payload is selected. Verification requires running the analysis first.',
      evidence: ['No analysis payload is available to cite yet (requires verification).'],
      whyItMatters: 'DevDNA only explains real discovered project data and never generates placeholder metrics.',
      nextSteps: ['Select an existing item or run a new analysis on this page.'],
      relatedTerms: ['static_analysis', 'grounded_explanation'],
    }
  }

  switch (kind) {
    case 'prediction':
      return explainPrediction(data, beginner)
    case 'change_intelligence':
      return explainChangeIntelligence(data, beginner)
    case 'architecture':
      return explainArchitecture(data, beginner)
    case 'what_if_lab':
      return explainWhatIfLab(data, beginner)
    case 'findings':
      return explainFindings(data, beginner)
    case 'experiments':
      return explainExperiments(data, beginner)
    case 'reports':
      return explainReports(data, beginner)
    default:
      return {
        title: 'Analysis Explanation',
        badge: 'Requires verification',
        whatCalculated: 'Unrecognised analysis type; unable to verify explanation rules for this view.',
        simpleMeaning: 'This metric or view requires verification against the current implementation.',
        evidence: ['No deterministic rule registered for this analysis type.'],
        whyItMatters: 'DevDNA avoids guessing when an implementation mapping is unavailable.',
        nextSteps: ['Inspect the raw evidence panel on this page.'],
        relatedTerms: ['static_analysis'],
      }
  }
}

function explainPrediction(prediction, beginner) {
  const impact = prediction.impact || {}
  const summary = impact.summary || {}
  const targetName = impact.target?.name || prediction.target_id || 'selected component'
  const definite = summary.definite ?? 0
  const possible = summary.possible ?? 0
  const routesAffected = summary.routes_affected ?? 0
  const routesTotal = summary.routes_total ?? 0
  const testsFail = summary.tests_predicted_fail ?? 0
  const testsMay = summary.tests_predicted_may_fail ?? 0
  const heuristic = impact.neuro_symbolic_simulation?.cascading_risk_score

  const evidence = [
    `Scenario: ${impact.scenario_label || prediction.scenario} targeting ${targetName}.`,
    `Definitely affected components: ${definite}; possibly affected (behind error handlers): ${possible}.`,
    `HTTP routes reached: ${routesAffected}/${routesTotal}.`,
    `Tests predicted to fail: ${testsFail} definite, ${testsMay} uncertain/handled.`,
  ]
  if (typeof heuristic === 'number') {
    evidence.push(`Deterministic structural risk heuristic: ${heuristic}/100 (not a failure probability; no AI used).`)
  }
  evidence.push('Validation status: Static prediction only — no experiment has been executed on this screen.')

  return {
    title: `Failure Prediction: ${impact.scenario_label || prediction.scenario}`,
    badge: 'Static prediction only',
    whatCalculated: beginner
      ? `DevDNA traced backwards through your code map starting from "${targetName}" to list every route, function, and test that relies on it—without running any code.`
      : `Dual Reverse BFS traversed propagating dependency edges from ${prediction.target_id || targetName}, separating unguarded paths (${definite} definite) from paths with matching try/except handlers (${possible} possible).`,
    simpleMeaning: beginner
      ? `If "${targetName}" goes down, up to ${definite + possible} parts of your app and ${routesAffected} API endpoints could be impacted. Because this is a map check (not a live test), it is a prediction, not a confirmed outage.`
      : `Static reachability indicates ${definite} unguarded dependent components and ${routesAffected}/${routesTotal} exposed routes. These are predicted outcomes until confirmed by a controlled experiment.`,
    evidence,
    whyItMatters:
      'Understanding predicted blast radius before an outage or refactor lets you add error handling, circuit breakers, or tests before users are affected.',
    nextSteps: [
      testsFail > 0
        ? `Open this prediction in the What-If Lab to validate the ${testsFail} predicted test failures with controlled fault injection.`
        : 'No covering tests were predicted to fail; check Test Gaps or add integration tests for this dependency.',
      'Expand affected components to inspect hop-by-hop dependency paths and any try/except handlers.',
    ],
    relatedTerms: ['reverse_bfs', 'blast_radius', 'static_analysis', 'experiment_validation'],
  }
}

function explainChangeIntelligence(analysis, beginner) {
  const risk = analysis.risk_assessment || {}
  const diff = analysis.graph_diff || {}
  const rec = analysis.recommendation || {}
  const val = analysis.validation
  const testsSummary = analysis.prioritized_tests?.summary || {}
  const direct = diff.summary?.directly_affected_count ?? 0
  const transitive = diff.summary?.transitively_affected_count ?? 0
  const routes = diff.summary?.impacted_routes_count ?? 0
  const score = risk.score ?? 0
  const level = risk.level || 'low'

  const evidence = [
    `Merge recommendation: ${rec.decision || 'PENDING'} (Composite change risk: ${score}/100, ${level}).`,
    `Directly modified components: ${direct}; transitively affected dependents: ${transitive}.`,
    `Exposed API routes in propagation path: ${routes}.`,
    `Prioritized tests selected: ${testsSummary.total_prioritized_tests ?? 0} (Tier 1 direct: ${testsSummary.tier_1_direct ?? 0}).`,
  ]
  if (val?.executed) {
    evidence.push(
      `Controlled validation executed (${val.mode || 'harness'}): baseline ${val.baseline_summary?.passed ?? 0} passed; faulted ${val.faulted_summary?.failed ?? 0} failed.`
    )
  } else if (val?.status === 'blocked') {
    evidence.push(`Controlled validation was BLOCKED (${val.reason || 'untrusted or disabled'}): no project code executed.`)
  } else {
    evidence.push('Controlled validation has NOT been executed yet; impact and test recommendations remain static predictions.')
  }

  return {
    title: `Change Intelligence: ${analysis.title || 'Code Change'}`,
    badge: val?.executed ? 'Empirically validated' : 'Static prediction only',
    whatCalculated: beginner
      ? `DevDNA compared your changed files against the project's dependency map, scored the change risk out of 100, and picked the most relevant tests to run.`
      : `Parsed diff/file changes into AST components, computed before-vs-after graph reachability, calculated a 6-factor weighted risk score (${score}/100), and ranked ${testsSummary.total_prioritized_tests ?? 0} tests across 5 priority tiers.`,
    simpleMeaning: beginner
      ? `This change touches ${direct} direct component(s) and can ripple into ${transitive} downstream component(s) and ${routes} API route(s). Current recommendation: ${rec.decision || 'review needed'}.`
      : `Risk score ${score}/100 (${level}) reflects structural blast radius, criticality, route exposure, findings, and uncertainty—not a statistical failure probability. ${val?.executed ? 'Empirical fault validation was executed.' : 'No runtime validation has run yet.'}`,
    evidence,
    whyItMatters:
      'Reviewing blast radius and running prioritized tests before merging catches cascading regressions early instead of discovering them in production.',
    nextSteps: [
      ...(rec.recommended_actions || []).slice(0, 2),
      !val?.executed
        ? 'Run Controlled Validation (where permitted) or execute the Tier 1 & Tier 2 prioritized tests locally.'
        : 'Review the Validation Report tab to compare predicted vs. observed test outcomes.',
    ],
    relatedTerms: ['git_diff', 'blast_radius', 'risk_scoring', 'regression_testing'],
  }
}

function explainArchitecture(arch, beginner) {
  const comps = arch.components || []
  const edges = arch.edges || []
  const summary = arch.summary || {}
  const confirmed = summary.edge_classification?.confirmed ?? 0
  const inferred = summary.edge_classification?.inferred ?? 0
  const unresolved = summary.coverage?.unresolved_calls ?? 0

  return {
    title: 'Architecture Dependency Graph',
    badge: 'Static AST graph',
    whatCalculated: beginner
      ? `DevDNA read your source code files and built a map of ${comps.length} components connected by ${edges.length} dependency arrows.`
      : `Static AST/Tree-sitter extraction built a NetworkX MultiDiGraph with ${comps.length} nodes and ${edges.length} edges (${confirmed} confirmed, ${inferred} inferred; ${unresolved} unresolved calls).`,
    simpleMeaning: beginner
      ? 'Each box is a real function, route, database, or test found in your code. Arrows point from the caller to the thing it needs. Clicking any node shows what depends on it.'
      : 'Directed edges encode `dependent → dependency`. Propagating edges drive reverse-BFS impact predictions and 0–100 structural criticality metrics; dynamic calls that could not be statically resolved are counted explicitly.',
    evidence: [
      `Discovered components: ${comps.length}; relationships: ${edges.length}.`,
      `Edge evidence: ${confirmed} confirmed directly from AST, ${inferred} inferred.`,
      `Unresolved dynamic calls: ${unresolved}.`,
    ],
    whyItMatters:
      'An evidence-backed architecture map stays synchronized with real code and exposes hidden coupling and single points of failure.',
    nextSteps: [
      'Click any high-criticality node to inspect its upstream dependents, downstream dependencies, and source file:line evidence.',
      'Use "Blast radius" on a node or open Failure Prediction to trace how failures propagate.',
    ],
    relatedTerms: ['ast', 'dependency_graph', 'traversal', 'betweenness_centrality'],
  }
}

function explainWhatIfLab(labState, beginner) {
  const { mode, prediction, experiment, step } = labState
  const expStatus = experiment?.status
  const blockedOrNotRun = !experiment || expStatus === 'blocked' || expStatus === 'queued' || expStatus === 'failed'

  const evidence = [
    `Current workflow step: ${step + 1} of 6.`,
    `Execution environment mode: ${mode === 'local' ? 'Local restricted harness (trusted sample)' : mode === 'docker' ? 'Docker sandbox (isolated)' : 'BLOCKED (no code execution permitted)'}.`,
  ]
  if (prediction) {
    evidence.push(`Selected prediction: ${prediction.impact?.scenario_label} on ${prediction.impact?.target?.name || prediction.target_id}.`)
  }
  if (experiment) {
    evidence.push(`Experiment status: ${expStatus}. ${expStatus === 'blocked' ? 'No project code was executed.' : ''}`)
  } else {
    evidence.push('No experiment has been executed yet.')
  }

  return {
    title: 'What-If Lab: Controlled Validation',
    badge: blockedOrNotRun ? 'Not yet validated' : `Experiment: ${expStatus}`,
    whatCalculated: beginner
      ? 'The What-If Lab takes a static failure prediction and tests it safely on a temporary copy of the project by injecting the fault and running the real test suite.'
      : 'Guided 6-step empirical validation workflow: select/synthesize fault spec → compute deterministic Reverse BFS impact → optional grounded AI explanation → explicit approval → baseline + faulted test run → confusion-matrix comparison.',
    simpleMeaning: beginner
      ? mode === 'blocked'
        ? 'Right now execution is blocked for safety (untrusted code without a Docker sandbox or server experiments disabled), so only static predictions are available—nothing will run.'
        : 'DevDNA runs your tests twice (normal baseline, then with the simulated outage) to prove whether the predicted failures actually happen.'
      : blockedOrNotRun
      ? 'No runtime validation has succeeded. Predictions remain static hypotheses until a permitted baseline + faulted experiment completes.'
      : `Experiment finished with status "${expStatus}". Compare TP/FP/TN/FN counts in Step 6 to evaluate static prediction accuracy.`,
    evidence,
    whyItMatters:
      'Controlled fault injection bridges the gap between static code assumptions and real runtime behavior while keeping production and host secrets isolated.',
    nextSteps: [
      mode === 'blocked'
        ? 'Use static Failure Prediction and Change Intelligence, or run locally with Docker enabled for untrusted Python repositories.'
        : 'Review isolation guarantees in Step 4 before approving execution, then inspect Prediction vs. Reality in Step 6.',
    ],
    relatedTerms: ['baseline', 'fault_injection', 'experiment_validation', 'false_positives'],
  }
}

function explainFindings(findingsData, beginner) {
  const list = findingsData.items || []
  const high = list.filter((f) => f.severity === 'high').length
  const medium = list.filter((f) => f.severity === 'medium').length
  const low = list.filter((f) => f.severity === 'low').length
  const suppressed = list.filter((f) => f.is_suppressed).length

  return {
    title: 'Architectural & Reliability Findings',
    badge: 'Static rule audit',
    whatCalculated: beginner
      ? `DevDNA checked your codebase against deterministic reliability and security rules and found ${list.length} item(s).`
      : `Rule-based static audit over the latest analysis graph produced ${list.length} findings (${high} high, ${medium} medium, ${low} low; ${suppressed} actively suppressed).`,
    simpleMeaning: beginner
      ? 'Findings highlight concrete structural risks—like missing error handling, untested routes, or hardcoded secrets—backed by exact file and line numbers.'
      : 'Each finding includes deterministic evidence, explicit confidence and assumptions, CODEOWNERS attribution, and auditable review/suppression state.',
    evidence: [
      `Loaded findings: ${list.length} (${high} high, ${medium} medium, ${low} low).`,
      `Suppressed findings: ${suppressed} (requires reason, owner, and expiry).`,
    ],
    whyItMatters:
      'Resolving high-severity structural findings reduces silent failure modes and hardens routes before incidents occur.',
    nextSteps: [
      high > 0 ? `Inspect the ${high} high-severity finding(s) first and review their cited file:line evidence.` : 'Review open medium/low findings or verify suppressions.',
      'Mark reviewed findings as acknowledged, resolved, or false positive to keep the audit trail accurate.',
    ],
    relatedTerms: ['static_analysis', 'dependency_graph', 'false_positives'],
  }
}

function explainExperiments(expData, beginner) {
  const experiments = expData.experiments || []
  const untested = expData.untested || []
  const executed = experiments.filter((e) => !['blocked', 'queued', 'running', 'failed'].includes(e.status))
  const blocked = experiments.filter((e) => e.status === 'blocked')

  return {
    title: 'Experiment History & Validation Status',
    badge: executed.length > 0 ? `${executed.length} executed` : 'No executed runs',
    whatCalculated: beginner
      ? `This page lists every controlled experiment recorded for this project (${experiments.length} total) and ${untested.length} static prediction(s) still waiting to be tested.`
      : `Persisted validation ledger: ${executed.length} completed empirical comparisons, ${blocked.length} blocked (not executed), and ${untested.length} unvalidated static predictions.`,
    simpleMeaning: beginner
      ? executed.length === 0
        ? 'No experiment has actually executed to completion yet. Blocked or untested entries are static predictions only—never confirmed failures.'
        : 'Click any executed experiment to see which predicted test failures matched reality and which were false alarms.'
      : 'Only completed experiments with a passing baseline and triggered fault produce precision/recall calibration metrics. Blocked runs execute zero code.',
    evidence: [
      `Executed experiments with verdicts: ${executed.length}.`,
      `Blocked experiments (no code executed): ${blocked.length}.`,
      `Unvalidated static predictions: ${untested.length}.`,
    ],
    whyItMatters:
      'Keeping executed experiments separate from unvalidated predictions prevents teams from mistaking static hypotheses for runtime proof.',
    nextSteps: [
      untested.length > 0
        ? `Pick one of the ${untested.length} unvalidated prediction(s) and click "Validate" to test it in the What-If Lab.`
        : 'Open any completed experiment to inspect per-test TP/FP/FN rows.',
    ],
    relatedTerms: ['experiment_validation', 'baseline', 'precision', 'recall'],
  }
}

function explainReports(reportData, beginner) {
  const reports = reportData.reports || []
  const experiments = reportData.experiments || []
  const aiLogs = reportData.aiLogs || []

  return {
    title: 'Reports & Audit Trail',
    badge: 'Persisted evidence',
    whatCalculated: beginner
      ? `DevDNA packages your saved architecture analysis, findings, experiments, and AI usage into downloadable Markdown or JSON reports with secrets automatically redacted.`
      : `Deterministic export layer reading persisted SQLAlchemy records (${reports.length} generated reports, ${experiments.length} experiment records, ${aiLogs.length} audited AI interactions).`,
    simpleMeaning: beginner
      ? 'Reports give your team a shareable snapshot of what was analysed, what was actually tested vs. only predicted, and every AI call made.'
      : 'Every exported report preserves the distinction between static predictions and executed experiment results, while the AI audit log tracks exact model, status, latency, and evidence references.',
    evidence: [
      `Previously generated report downloads: ${reports.length}.`,
      `Experiment reports available: ${experiments.length}.`,
      `Audited AI interactions logged: ${aiLogs.length}.`,
    ],
    whyItMatters:
      'Redacted, reproducible reports and AI audit logs support engineering reviews, incident post-mortems, and security compliance.',
    nextSteps: [
      'Download a Markdown project report for PRs or architecture reviews, or JSON for automated pipelines.',
      'Inspect the Gemma audit log to verify every AI request status and model used.',
    ],
    relatedTerms: ['grounded_explanation', 'llm', 'experiment_validation'],
  }
}
