# Plan 0096: CLI Run Watch Sequence Cursor

## Status
Complete

## Goal
Let an operator restart `run watch` from an explicit durable history sequence
without duplicating entries already observed or skipping entries appended
after the previous watcher stopped.

## Tasks
- [x] Add `run watch --after-sequence N`, defaulting to zero, and validate the
      operator-provided cursor before opening the state database.
- [x] Initialize the existing in-process watch cursor from that value while
      retaining the read-only polling and terminal/interrupt behavior.
- [x] Add focused tests for validation, duplicate-free and gap-free restart,
      durable sequence output, and non-mutation.
- [x] Run focused and full verification, refresh the committed code index,
      update the durable run record, commit, push, and close the issue.

## Resume Notes
Selected active-milestone issue: #102 (Sprint 16, priority:2, agent-ready;
its sole blocker #101 is closed). The explicit cursor extends Plan 0095's
in-process sequence tracking and does not add automatic watcher state,
persistence, subscriptions, or transport.
