# Plan 0073: Dispatch Persisted Sandbox Policy

## Status
Complete

## Goal
Execute command steps from their durable sandbox policy, resolving environment
passthrough names only at dispatch time and rejecting competing per-invocation
sandbox configuration before the queued step changes state.

## Tasks
- [x] Add a policy-aware runtime dispatch boundary while preserving the legacy
      executor path for command steps without a persisted policy.
- [x] Resolve persisted environment names from the executing process and build
      the complete container sandbox from the persisted policy.
- [x] Reject per-invocation sandbox flags for persisted-policy steps before
      dispatch and preserve provider-message behavior.
- [x] Cover persisted-policy dispatch, environment resolution and redaction,
      conflict rejection, network behavior, and legacy compatibility.
- [x] Update operator guidance, refresh the code index, and run full verification.

## Resume Notes
Selected active-milestone issue: #72. Issue #71 is closed, so the persisted
`SandboxPolicy` contract is available. This issue consumes that policy at
dispatch; it does not add templates, inheritance, editing, environment files,
mount inference, host networking, or worker-loop behavior.

Implementation complete. The runtime requires a policy resolver for persisted-policy
steps and rejects an injected executor that could bypass that policy. The CLI resolves
passthrough names from the dispatching process, constructs the exact persisted spec,
and rejects every legacy sandbox flag before starting the step. Recorded engine
commands retain passthrough names but redact resolved values. Legacy unpersisted
command steps and provider-message steps keep their established dispatch paths.
