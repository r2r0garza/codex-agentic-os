# Plan 0090: Resolve Capability-Routed Provider Steps

## Status
Complete

## Goal
Resolve a provider-message step's required capability deterministically from an
explicit ordered provider policy, dispatch through the selected provider/model,
and persist inspectable routing provenance without changing fixed-provider
behavior.

## Tasks
- [x] Add an ordered, inspectable provider routing policy and deterministic resolver.
- [x] Resolve capability-routed messages at dispatch while fixed-provider messages bypass routing.
- [x] Persist the required capability, selected provider/model, and stable selection reason in durable history.
- [x] Cover policy-order tie-breaking, repeatability, restart reconstruction, CLI inspection, and fixed-provider regression behavior.
- [x] Rebuild/check the index, run focused and full tests, and run `git diff --check`.

## Resume Notes
Selected active-milestone issue: #94 (priority:2, sole unblocked
`agent-ready` issue in Sprint 14 "Capability-based provider routing"). Issue
#95 remains blocked and owns definitive no-eligible-provider failure handling.

Implemented `ProviderRoutingPolicy`/`ProviderRoute`, the default registry-order
policy, repeated CLI `--provider-preference` overrides for `run execute-next`
and `worker run`, and read-only `provider routing-policy` inspection. A
capability-routed dispatch gives the adapter a transient fixed-provider
message while leaving the durable step's provider-neutral requirement
unchanged. The atomic `step_started` history entry records the capability,
resolved provider/model, and deterministic reason through both queued-run and
already-running paths. Fixed-provider steps bypass policy resolution.

Full suite: `604 passed`. Rebuilt index: 24 files, 918 symbols, 5275
relationships; `codex-agentic-os index check` and `git diff --check` pass.
