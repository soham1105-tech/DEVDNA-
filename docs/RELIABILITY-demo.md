# Reliability-memory demo — recorded experiment outcomes

`GET /api/projects/6a9d0025ab84/reliability` on the running DevDNA server.
The project has **one real completed experiment** (`worker_unavailable` fault
injection) whose per-test outcomes were recorded by the calibration pipeline —
no data in this demo was seeded or simulated.

## Summary

- Observations: **12** from **1** completed experiment(s)
- Distinct scenarios: 1; distinct claim patterns: 3

## Scenario memory

- Scenario `worker_unavailable`: 12 observations, 1 experiment
- fail-prediction accuracy: **insufficient_data** (sample 1)
- pass-prediction accuracy: **insufficient_data** (sample 8)
- uncertain observations: 3; tests that actually broke: 1

## Pattern memory (contradiction rate per claim shape)

| pattern | observations | contradiction rate |
|---|---|---|
| `guard=False|mocked=False|certainty=definite|routes=True` | 1 | insufficient_data |
| `guard=False|mocked=False|certainty=unaffected|routes=True` | 8 | insufficient_data |
| `guard=True|mocked=False|certainty=possible|routes=True` | 3 | insufficient_data |

## The honest headline

Every rate here is `insufficient_data`: 12 observations is below the
20-sample floor, so the memory refuses to print a percentage.
This is the intended behaviour — one experiment cannot establish a track record,
and printing "100% accurate" from one run would be exactly the kind of
overclaim DevDNA exists to prevent. As more experiments run, these entries
flip to `measured` with explicit sample sizes.

An empty project gets `status: "empty"` with instructions to run controlled
experiments — nothing is inferred or simulated.
