# Plan 0120: Resume and Cancel Bounded Tool Loops Safely Between Iterations

## Status
Complete

## Goal
Make a bounded tool-call loop safely recoverable and cancellable between
iterations: a replacement worker resumes provider continuation from the last
completed durable iteration without repeating a finished sandbox execution,
and an operator cancellation between iterations stops the loop cleanly with
completed iterations preserved.

## Tasks
- [x] Distinguish a genuinely uncertain in-progress tool phase (`requested`)
      from a safe, fully durable boundary (`executed`) when `execute_next_step`
      finds a pre-existing `running` step.
- [x] Resume a step at a safe `executed` boundary by replaying its durable
      iterations into a fresh provider request instead of failing it.
- [x] Extract the tool-call loop body shared by fresh dispatch and resume
      into one helper so both paths enforce the same budget, undeclared-tool,
      and sandbox-resolver contracts identically.
- [x] Detect a concurrent cancellation between iterations (a proactive status
      check at the top of every loop pass, plus a reactive check when a
      durable write conflicts) and stop without an additional provider
      request or sandbox execution.
- [x] Cover worker-loop replacement, runtime resume/replay, and cancellation
      with focused tests; run the full suite; refresh the index.

## Resume Notes
Selected active-milestone issue: #133 (Sprint 22 "Bounded agentic tool loop",
priority:2, `agent-ready`), the sole unblocked issue; #134 remains correctly
blocked on it.

`execute_next_step`'s pre-existing-`running_step` check now branches three
ways instead of two: a delegation step still reconciles from its child's
outcome; a tool-declaring step whose last durable iteration is `executed`
resumes through `_resume_tool_loop`; everything else (a plain command/provider
step, or a tool step whose last iteration is still `requested`) keeps the
original "run already has a running step" failure, which is the existing
explicit `run recover` contract for genuinely uncertain in-progress execution.
This is sound because of a pre-existing durable invariant enforced by
`_validate_tool_iterations`: only the *final* stored iteration may be
anything other than `executed`, so an `executed` last iteration implies every
prior iteration is too — the full turn history is safe to replay from stored
evidence alone.

`_resume_tool_loop` rebuilds the exact message sequence a fresh dispatch would
have accumulated in memory (system, resolved context, user, then each
iteration's assistant/tool turn pair) via the new
`_replay_tool_iteration_messages`, re-resolves provider routing exactly like
fresh dispatch (there is no durable "resolved route" to reuse — fresh dispatch
recomputes it too), and issues one provider request before handing off to the
shared `_advance_tool_loop`, which both the fresh-dispatch and resume paths
now call. This keeps the loop's budget, undeclared-tool-rejection, and
sandbox-resolver contracts defined exactly once.

Cancellation between iterations is checked two ways rather than relying on
one. Proactively, the top of `_advance_tool_loop`'s `while` body re-reads the
run and step before acting on the response it already holds; if either left
`running` (e.g. an operator cancelled while the model was producing that
response), the loop returns the cancelled pair immediately, persisting
nothing and executing nothing further for that round. Reactively, the two
CAS-guarded persist helpers (`_persist_tool_call_requested`,
`_persist_tool_call_executed`) now check, on a write conflict, whether the
conflict was actually a concurrent cancellation (rather than some other
unexpected concurrent writer) and signal it via an internal
`_ToolLoopCancelled` exception instead of the generic "step transition
conflict" `ValueError`; `_advance_tool_loop` catches it at each of the two
call sites and returns the now-cancelled pair. This covers the case where a
cancellation lands while a sandbox command is actually executing: the
in-flight execution is allowed to finish (killing an already-launched
container is out of scope, matching #134's excluded "engine-level crash
reconciliation"), but its result is not persisted once cancellation has
already superseded the step, so no completed evidence is fabricated after the
fact.

`recover_running_step` is unchanged: it remains the operator's explicit,
unconditional "fail this running step" tool for cases the automatic resume
path does not cover (a `requested`-phase step, a plain command/provider step,
or an operator who wants to force-fail a resumable-looking step anyway).

Verification: 4 new focused runtime tests (resume after an executed-phase
interruption without re-executing the tool, resume replaying the completed
iteration as provider context, cancellation detected between iterations via
the proactive check, and cancellation detected mid-execution via the reactive
CAS-conflict path) plus 1 new worker test proving a fresh `run_worker`
process, sharing only durable state with a crashed one, completes a
multi-iteration tool task without repeating the sandbox call. Full `pytest`
passed 858 (up from 853). Index rebuild and `index check`/`git diff --check`
run as part of completion.
