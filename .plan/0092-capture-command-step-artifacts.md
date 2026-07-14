# Plan 0092: Capture Command-Step Artifacts

## Status
Complete

## Goal
Let a command step declare named workspace artifact paths and capture their
content into durable local artifact storage with hash, size, and producing
run/step identity after a successful execution, without corrupting the
step's own success/failure outcome when a declared file is absent or
oversized.

## Tasks
- [x] Add a durable `ArtifactDeclaration` (name, path) on `RunStep`, validated
      before mutation: requires a persisted sandbox policy with at least one
      mount, and the declared path must resolve within a mount's
      container-side path.
- [x] Extend `run add-step` with repeatable `--artifact NAME=PATH`, scoped to
      command steps with a persisted sandbox policy.
- [x] Preserve declared artifacts across every existing step payload rewrite
      (dispatch, completion, failure, recovery, approval decision), matching
      the sandbox-policy preservation contract from Plan 0072.
- [x] After a successful command result, resolve each declared container path
      to its host-side path through the persisted mounts and capture present
      files (content hash, size) into local artifact storage; record absent
      files explicitly; reject oversized files without reading their content,
      recording the configured limit instead.
- [x] Record artifact capture/absence/rejection in durable run history
      (new `artifact_name` column) without exposing command arguments,
      environment values, credentials, or terminal output.
- [x] Show declared and captured artifacts through `run inspect` /
      `run inspect-step`.
- [x] Cover validation, capture, absence, size-limit rejection, history
      redaction, and multi-step dispatch preservation with focused tests; run
      the full suite.

## Resume Notes
Selected active-milestone issue: #97 (Sprint 15, priority:1, agent-ready, no
blocker). Issues #98 (provider-response artifacts) and #99 (list/export CLI)
remain `blocked` on this issue's storage contract.

While threading artifact preservation through every step payload rewrite, found
that `RunCoordinator.transition_step` never persisted `sandbox_policy` when
dispatching a step in a run that was already `RUNNING` (i.e. any command step
after the first in a run) — a pre-existing gap since Plan 0072. Fixed it in the
same change, since it would otherwise have silently dropped both the sandbox
policy and artifact declarations for every non-first command step. Verified by
a dedicated multi-step test that dispatches and completes a second command
step and confirms its persisted policy and declarations, and captured
artifact, survive that path.

Artifact metadata is a new `artifact` state kind (SQLite `state_records`,
migrated in place like the existing `step` kind) plus content bytes written to
a local `<state-db-dir>/artifacts/` directory, overridable via
`RunCoordinator(..., artifact_storage_dir=..., artifact_size_limit_bytes=...)`
(default 10,000,000 bytes). Full suite `634 passed` (up from 607, +27 net);
rebuilt and confirmed the index (24 files, 953 symbols, 5539 relationships);
`git diff --check` clean.
