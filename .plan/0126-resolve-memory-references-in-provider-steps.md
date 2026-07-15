# Plan 0126: Resolve Explicit Memory References in Provider Steps

## Status
Complete

## Goal
Let a provider-message step declare an ordered set of named memory
references, validated against durable memory entries before mutation, and
resolve their bodies into the provider payload at dispatch using the
established Sprint 11 context mechanics.

## Tasks
- [x] Add `memory_names` to the durable `RunStep` model and payload,
      preserved across every lifecycle rewrite alongside `context_step_ids`.
- [x] Validate declared memory names before step mutation: non-empty,
      unique, require a provider message, and resolve against durable
      `memory_entry` records; unknown names reject before any write.
- [x] Resolve referenced memory bodies at dispatch as `(user, assistant)`
      message pairs — the same provider-neutral shape context references
      use — ordered before context-step turns and the step's own current
      message, reaching every adapter family unchanged.
- [x] Record declared memory names (not bodies) on the `step_started`
      history entry as evidence resolution was attempted.
- [x] Fail a step explicitly, before provider contact, when a declared
      memory name no longer resolves at dispatch (defensive: memory entries
      have no delete/edit path today), recording a safe reason via the
      existing `fail_step_from_error` path.
- [x] Add `run add-step --memory` CLI support and inspection output
      (names only, never resolved bodies).
- [x] Cover validation, lifecycle persistence, dispatch ordering/history,
      missing-at-dispatch failure, adapter payload construction (OpenAI-
      compatible, Anthropic, Google), and CLI validation/inspection.
- [x] Rebuild/check the index, run the full suite, and run `git diff --check`.

## Resume Notes
Selected active-milestone issue: #141 (Sprint 24 "Durable cross-run agent
memory", priority:1, `agent-ready`), the sole unblocked ready issue; #142
remained blocked on this issue.

Implementation reuses `MemoryRegistry(self.store)` on demand inside
`RunCoordinator`, the same pattern `ExecutionPolicyRegistry` already uses,
rather than holding a registry reference. `_resolve_memory_messages` mirrors
`_resolve_context_messages` exactly in shape (one alternating pair per
reference) so no adapter-level code changes were needed. The missing-memory
failure path is intentionally different from unresolved context references:
context references can legitimately not-yet-exist (an earlier step hasn't
succeeded yet) and keep the step queued via
`ContextReferencesUnresolvedError`; a validated memory name has no such
legitimate "not yet" state in this slice (memory is immutable and creation
already checked existence), so a name that fails to resolve at dispatch is
treated as a hard, terminal step failure instead.

Verification: activated `.venv`; 11 new focused runtime/adapter/CLI tests
passed (6 runtime, 3 adapter payload, 2 CLI). Full `pytest` passed 953 (up
from 941). The index rebuilt to 30 files / 1447 symbols / 8396 relationships
and `index check` reported current; `git diff --check` passed.
