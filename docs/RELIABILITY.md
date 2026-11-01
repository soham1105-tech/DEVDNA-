# Reliability memory (Module 5)

A durable track record of how DevDNA's predictions have fared against
**observed** experiments — so users can see how much to trust each kind of
claim, backed by data instead of confidence labels.

## Endpoints

- `GET /api/projects/{project_id}/reliability` — one project's memory
- `GET /api/reliability` — global (or `?project_id=` to scope)

```json
{
  "status": "populated",
  "summary": {"observations": 612, "experiments": 4, "scenarios": 3, "patterns": 6,
               "min_sample_for_rates": 20},
  "by_scenario": [{
      "scenario": "db_unavailable", "observations": 246, "experiments": 2,
      "fail_prediction_accuracy": {"status": "measured", "sample": 210, "rate": 0.976},
      "pass_prediction_accuracy": {"status": "measured", "sample": 24, "rate": 1.0},
      "excluded_observations": 12, "tests_broke": 205}],
  "by_pattern": [{
      "pattern_key": "...", "guard_present": false, "test_mocked": false,
      "certainty": "definite", "has_route_path": true,
      "observations": 180, "contradiction_rate": {"status": "measured", "sample": 180, "rate": 0.022}}],
  "method": "Aggregates per-test PredictionOutcome rows persisted by completed experiments ...",
  "limitations": ["Memory covers only claims that were experimentally observed ..."]
}
```

## What it aggregates

Every completed experiment persists one `PredictionOutcome` row per compared
test (prediction vs observed outcome, plus the claim's pattern features:
guard present, test mocked, certainty, route path). The memory groups those
rows by **scenario** (fail-prediction accuracy, pass-prediction accuracy,
uncertain/excluded counts) and by **claim pattern** (contradiction rate for
"definite and unguarded" style claims) — the same patterns the calibration
engine keys on, so the two views agree by construction.

## Honesty rules

- **Empty is a first-class state.** With no recorded experiments the response
  is `status: "empty"` and says exactly how to build a memory. Nothing is
  inferred or simulated.
- **Rates below `MIN_SAMPLE_FOR_CALIBRATION` (20) are `insufficient_data`**,
  never a fluke 0%/100%.
- `excluded` observations (test failed at baseline / did not run) carry no
  evidence and never enter an accuracy numerator or denominator — they are
  counted separately.
- A `mismatch` means the prediction was wrong for that test in that
  experiment; it does not explain why (the per-test comparison rows on the
  experiment retain the details).
- Memory covers only experimentally observed claims; unobserved predictions
  are absent, and the response says so.

## Relationship to calibration

Calibration (existing) answers *"how likely is this specific prediction to
hold, given past outcomes?"* and feeds scenario explanations. Reliability
memory (this module) answers *"how good has DevDNA been, overall and per
claim pattern?"* — an audit view over the same outcome rows.
