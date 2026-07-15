# Plan 0125: Persist Durable Named Memory

## Status
Complete

## Goal
Persist immutable named decision and note entries with optional creating
agent/run/step provenance, reject duplicate names atomically, and expose stable
read-only CLI listing and inspection across fresh processes.

## Tasks
- [x] Add a `memory_entry` durable state kind and migrate existing databases.
- [x] Add typed memory creation, validation, stable listing, and inspection.
- [x] Add `memory create|list|inspect` CLI commands with read-only reads.
- [x] Cover validation, duplicate races, restart persistence, ordering, and CLI errors.
- [x] Document the user-facing commands, refresh the index, and verify the suite.

## Resume Notes
Selected active-milestone issue: #140 (Sprint 24 "Durable cross-run agent
memory", priority:1, `agent-ready`), the sole unblocked ready issue. Issues
#141 and #142 remain blocked on this persistence slice.

Provider-step references, dispatch-time payload resolution, automatic or
semantic retrieval, entry mutation/deletion, expiry, retention, and
model-initiated writes are explicitly out of scope for #140.

Implementation uses the existing insert-only `StateStore.insert()` primitive,
whose immediate SQLite transaction and `(kind, key)` primary key make memory
names atomic across competing store connections. Entries retain their supplied
body plus the finite `decision`/`note` kind, UTC creation time, and only the
explicit optional agent/run/step provenance fields. Reads defensively validate
persisted shapes; list ordering follows the store's stable key ordering.

Verification: 68 focused memory/state tests passed, including simultaneous
duplicate writers, migration, fresh read-only store/CLI processes, stable
ordering, and deterministic invalid/unknown paths. Full `pytest` passed 941.
The index rebuilt to 28 files / 1407 symbols / 8177 relationships and `index
check` reported current; `git diff --check` passed.
