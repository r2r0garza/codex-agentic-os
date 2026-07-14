# Plan 0094: List and Export Run Artifacts

## Status
Complete

## Goal
Expose read-only CLI commands so an operator can inspect a run's captured,
absent, and rejected artifacts in stable order and export one captured
artifact's content byte-identical to a chosen path, for both command-step and
provider-response artifacts.

## Tasks
- [x] Add `RunCoordinator.read_artifact_content(artifact_id)` to return one
      captured artifact's stored bytes, read-only, rejecting unknown, absent,
      rejected, and locally-missing artifacts with clean errors.
- [x] Add `run list-artifacts RUN_ID [--step STEP_ID]`, printing each artifact
      record with explicit run id, step id, name, status, content hash, and
      size, in the existing stable `(step_id, name)` order.
- [x] Add `run export-artifact RUN_ID --name NAME [--step STEP_ID]
      --destination PATH`, resolving the named artifact (disambiguated by
      `--step` when more than one step declares the same name) and writing its
      stored content byte-identical to the destination.
- [x] Fail cleanly without writing the destination or mutating durable state
      for a missing run, unknown name, ambiguous name, or an absent/rejected
      artifact.
- [x] Cover stable ordering, redaction, filtering, byte-identical export,
      every clean-failure path, non-mutation, and both command-step and
      provider-response artifacts with focused tests; run the full suite.
- [x] Document the new commands in DEVELOPMENT.md.

## Resume Notes
Selected active-milestone issue: #99 (Sprint 15, priority:3, agent-ready, no
remaining blocker after #97/#98 closed). This closes Sprint 15's stated exit
criterion for read-only artifact listing and export.

`list_artifacts` already existed from Plan 0092; only the export read path was
missing. `read_artifact_content` reuses the same `_artifact_storage_dir`
layout and `ArtifactStatus` classification, so no new storage contract was
introduced. `list-artifacts`/`export-artifact` reference artifacts by name
(matching the operator-facing `--artifact NAME=PATH` / `--response-artifact
NAME` declaration vocabulary) rather than the internal `artifact_id`, so
`--step` exists specifically to disambiguate a name declared by more than one
step in the same run — both `list-artifacts` and `export-artifact` reuse the
existing `step_id` filter on `list_artifacts` for this. Full suite: `660
passed` (up from 644, +16 net); rebuilt and confirmed the index (24 files, 993
symbols, 5835 relationships); `git diff --check` clean.
