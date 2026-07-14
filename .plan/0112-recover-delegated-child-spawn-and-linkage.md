# Plan 0112: Recover Delegated Child Spawn and Linkage

## Status
Complete

## Goal
Harden parent-child delegation so recovery paths never leave an active
delegated child orphaned or reconcile against a mismatched linkage, without
touching the already-atomic spawn transaction from #121.

## Tasks
- [x] Cascade `RunCoordinator.cancel` to an active (`queued`/`running`)
      delegated child run, and that child's own active steps, atomically
      alongside the parent's cancellation. A child already at a terminal
      status is left untouched.
- [x] Reject `RunCoordinator.recover_running_step` outright for a running
      delegation step: its `running` status is a legitimate parked state
      while a child executes, not an uncertain in-process execution the
      generic crash-recovery path is meant for.
- [x] Defensively reject reconciliation (`_reconcile_delegation_step`) and
      cancellation cascade against a child run whose `parent_run_id`/
      `parent_step_id` no longer matches the delegating parent/step, rather
      than silently treating an unrelated run as the linked child.
- [x] Cover cascade-cancel (with and without the child's own active step),
      terminal-child-left-untouched, mismatched-linkage rejection (cancel
      and reconcile paths), and delegation-step recovery rejection with
      focused tests.
- [x] Refresh the code index and run proportional verification.

## Resume Notes
Selected active-milestone issue: #123 (Sprint 20 "Parent-child run
delegation", priority:2, `agent-ready`, the sole unblocked issue; #124
remained correctly `blocked` on it).

The atomic single-transaction spawn from #121
(`StateStore.dispatch_delegation_step`, one `BEGIN IMMEDIATE` block) already
closes the literal "interruption between spawn and linkage" window described
in #123's objective — there is no partial-write state to recover from there,
and the existing `DelegationPendingError` reconciliation path from #122
already lets a later worker safely re-poll a parked delegation step without
creating a duplicate child. Auditing the surrounding recovery surfaces
instead turned up two real orphaning paths this issue closes:

1. `RunCoordinator.cancel` cancelled a parent run's active delegation step
   but never touched its linked child run, which kept executing
   indefinitely under its own agent with no parent step left to ever
   reconcile its outcome — silently orphaned by the parent's own explicit
   cancel. `cancel` now gathers the whole cancel closure (parent, and any
   active delegated child, recursively) into one `records`/`expected`/
   `history` list and commits it through a single `StateStore.put_many`
   call, so the cascade is atomic with the parent's own cancellation. A
   child already at a terminal status (e.g. it succeeded moments before the
   operator cancelled the parent, racing ahead of reconciliation) is left
   untouched rather than overwritten. This is the one automatic
   child-cancellation policy the Sprint 20 milestone's scope boundary
   names ("no automatic child cancellation policy beyond the parent's
   explicit cancel").
2. `RunCoordinator.recover_running_step` (the CLI's `run recover`, for
   failing a running command/provider step whose subprocess or adapter call
   may have crashed without a durable result) had no delegation-specific
   guard. Calling it on a running delegation step would fail the parent
   step and run while its child kept running completely unaware and
   unlinked to any live tracking — a second, operator-triggered way to
   orphan a child. It now rejects delegation steps outright with a message
   pointing at the linked child run, since the correct recovery for a truly
   stuck delegation is recovering the *child's own* running step (ordinary,
   already-supported recovery) or waiting for `_reconcile_delegation_step`
   to resolve the parent once the child reaches a terminal status.

Both cascade-cancel and reconciliation also now verify the child run's
`parent_run_id`/`parent_step_id` still points back to the delegating
parent/step before treating it as the legitimate linked child, rejecting
explicitly on a mismatch instead of silently cancelling or completing
against an unrelated run. This directly covers the acceptance criterion
about malformed/conflicting linkage records; a real mismatch can only arise
from direct state-store tampering today (the deterministic `{step_id}-child`
id and single-transaction spawn make legitimate linkage always consistent),
so it is tested by deliberately rewriting a child run's payload through the
raw `StateStore` rather than through any reachable coordinator path.
`_collect_cancel_closure`'s own `visited` set is a similar defensive-only
guard: no reachable path can currently construct a delegation cycle (child
run ids are always freshly minted, never reused), and cycle rejection at
declaration time is explicitly #124's scope, not this issue's.

Verification: activated `.venv`; focused delegation runtime tests 22 passed
(up from 16, +5 new: cascade-cancel with the child's own active step,
terminal-child-left-untouched, cancel-path linkage-mismatch rejection,
recover-running-step delegation rejection, reconcile-path linkage-mismatch
rejection; the pre-existing cancel/delegation test also gained an assertion
that the child is now cancelled); full `pytest` 791 passed (up from 786);
`codex-agentic-os index build` refreshed 27 files / 1143 symbols / 7016
relationships; `index check` current; `git diff --check` clean. No CLI code
changes were needed: `run cancel` and `run recover` already call straight
through to `RunCoordinator.cancel`/`recover_running_step`, and the CLI's
existing top-level exception handling surfaces the new `ValueError` messages
unchanged.
