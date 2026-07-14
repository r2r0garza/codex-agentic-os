# Plan 0093: Provider Response Artifacts

## Status
Complete

## Goal
Let a provider-message step declare a named response artifact and capture the
normalized response content through the same durable artifact metadata,
content-storage, hashing, size-limit, inspection, and history contracts used
by command-step artifacts.

## Tasks
- [x] Add and validate an explicit provider response-artifact name before mutation.
- [x] Preserve the declaration across every step lifecycle payload rewrite.
- [x] Capture successful normalized response content through shared artifact storage.
- [x] Record provider provenance and redacted artifact history.
- [x] Cover runtime, routing/context/usage regressions, CLI creation/inspection, restart, and size rejection.
- [x] Refresh the code index and run full verification.

## Resume Notes
Selected active-milestone issue: #98 (Sprint 15, priority:2, agent-ready, no
remaining blocker after #97 closed). Issue #99 remains blocked on this issue.

Implemented `RunCoordinator.add_step(..., response_artifact_name=...)` and
`run add-step --response-artifact NAME` for provider-message steps. Successful
normalized response content is UTF-8 encoded and passed through the same
artifact persistence helper as command artifacts, including local content
storage, sha256, byte size, size-limit rejection, run/step identity, and
redacted history. Existing output, usage, routing, fixed-provider, and context
contracts remain unchanged. Full suite: 644 passed; committed index refreshed
to 24 files, 972 symbols, and 5631 relationships; `git diff --check` clean.
