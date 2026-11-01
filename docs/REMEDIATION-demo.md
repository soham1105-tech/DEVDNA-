# Verified-remediation demo — DevDNA's own backend

Project: DevDNA's `backend/` uploaded and analysed (`6d481a675e0a`).

## The finding

`5db838531133` — `http_without_timeout` (medium): *"Outbound HTTP call without an
explicit timeout (httpx.Client.stream)"* in `app/api/projects.py:112`, the GitHub
archive download.

## The proposed remediation

Add an explicit per-call timeout to the streaming request (the client-level
`httpx.Timeout(30, connect=5)` exists, but the rule asks for an explicit call-site
timeout):

```diff
- with client.stream("GET", url, headers={"Accept": ..., "User-Agent": "DevDNA"}) as response:
+ with client.stream("GET", url, timeout=30, headers={"Accept": ..., "User-Agent": "DevDNA"}) as response:
```

## Verification result

- Checked: **1** finding (via `finding_ids`)
- `resolved_static`: **1** — the finding's fingerprint is absent from the patched analysis
- `still_present`: **0**
- Introduced findings: **0** (the full finding set of the run anchors this comparison)
- Patched copy analysed independently: 892 components / 2749 edges

## What was NOT done

- The original `app/api/projects.py` was not modified (the patch was applied to a
  disposable copy which was deleted afterwards).
- No tests were executed. `resolved_static` is a static-analysis claim, not proof of
  correctness; pair it with a controlled experiment for behavioural evidence.
