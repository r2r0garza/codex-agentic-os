# Plan 0018: Atomic Run Creation

## Status
Complete

## Goal
Ensure competing coordinators cannot overwrite the same queued run identifier.

## Tasks
- [x] Add an insert-only state-store operation that rejects an existing identity inside the write transaction.
- [x] Use insert-only persistence for queued run creation while preserving validation and payloads.
- [x] Prove duplicate creation through separate coordinators leaves the original run unchanged.

## Resume Notes
Selected queue issue: #9. `RunCoordinator.create()` now persists queued runs through
`StateStore.insert()`, whose SQLite primary-key check occurs inside an immediate write
transaction. Successful runs begin at revision 1; competing duplicate creation cannot
replace the original payload or revision. Resume with the next prioritized unblocked
`agent-ready` GitHub issue.
