# Plan 0072: Persist Command-Step Sandbox Policy

## Status
Complete

## Goal
Let `run add-step` durably persist a command step's complete sandbox policy
(kind, image, mounts, working directory, environment passthrough names,
network opt-in) so the step can later execute reproducibly, and show that
policy through read-only inspection without ever storing or exposing raw
environment values.

## Tasks
- [x] Add a durable `SandboxPolicy` view on `RunStep`, validated independently
      of the CLI so library callers get the same guarantees.
- [x] Extend `run add-step` with per-step sandbox policy flags, scoped to
      command steps only.
- [x] Persist environment passthrough as variable names only; never persist
      `KEY=VALUE` pairs or resolved values.
- [x] Preserve the persisted policy across every existing step payload
      rewrite (dispatch, cancel, approval decision, completion, failure,
      recovery) so it is not silently dropped after creation.
- [x] Show the persisted policy through `run inspect` / `run inspect-step`.
- [x] Cover persistence, restart reload, validation, and command-only scope
      with focused tests; run the full suite.

## Resume Notes
Selected active-milestone issue: #71. Dispatch-time consumption of the
persisted policy (resolving passthrough values, rejecting conflicting
per-invocation `execute-next` flags) is explicitly out of scope and reserved
for the follow-up issue #72.

Implementation complete for #71. Fresh-process CLI UAT persisted a policy,
reloaded it from a fresh process, rejected a malformed policy before any
mutation, and confirmed the durable SQLite payload holds only passthrough
variable names, never resolved environment values.
