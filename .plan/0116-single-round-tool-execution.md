# Plan 0116: Single-Round Declared Tool Execution

## Status
Complete

## Goal
Execute one model-requested declared tool through the existing sandbox
boundary, persist the request before execution and the result after
execution, and return the result for the model's final response in a
recovery-safe single-round flow.

## Tasks
- [x] Extend `ChatMessage`/`ChatResponse` with a normalized `ChatToolCall`
      so an adapter response can carry a model-requested tool call, and a
      prior assistant/tool turn can be replayed into a follow-up request.
- [x] Map tool-call requests and results into and out of the OpenAI-compatible,
      Anthropic, and Google native payload shapes.
- [x] Add a durable `ToolCallRecord`/`ToolCallPhase` and `RunStep.tool_call`
      field, written in two phases (`requested` before the sandboxed command
      runs, `executed` once its result is durable) so a step interrupted at
      either phase never silently re-executes the tool.
- [x] Wire `execute_next_step` to detect a tool call in the adapter response,
      reject an undeclared tool name and a second tool call in the follow-up
      response, execute the declared command template unmodified through the
      step's persisted sandbox policy, and issue the single follow-up request.
- [x] Preserve `tool_call` across every existing step-payload rewrite path
      that already preserves provider messages, and merge non-sensitive tool
      evidence into the terminal step `output`.
- [x] Add focused adapter, runtime, worker, and CLI tests; run the full
      suite; refresh the index.

## Resume Notes
Selected active-milestone issue: #128 (Sprint 21 "Durable model tool
calling", priority:1, `agent-ready`), the sole unblocked issue; #129 remains
correctly `blocked` on it.

The declared tool's command template executes unmodified — the model's
requested arguments are recorded as durable evidence only and are never
interpolated into the sandboxed command, so a tool call cannot inject
arguments into the executed argv. This follows directly from Plan 0114's
scope ("only the declaration is persisted, not its execution") and keeps the
sandbox boundary's existing safety properties unchanged.

Recovery-safety follows from the project's existing convention rather than a
new state machine: `recover_running_step` never resumes a running step, it
only ever fails it definitively. Preserving `tool_call` (whichever phase was
last durably written) in that failure payload is therefore sufficient —
there is no path that resumes a step after either the `requested` or
`executed` phase and re-executes the tool. A sandbox or provider call raising
an exception outside `(ValueError, RuntimeError, NotImplementedError)` (for
example a timeout) is left uncaught by `execute_next_step`, exactly matching
how a command step's own sandbox `execute()` failure is already handled;
the durable phase already written before that call is what recovery later
inspects.

The `sandbox_resolver` requirement for tool execution is intentionally lazy:
a tool-declaring provider step no longer requires a resolver just to be
dispatched (an existing test asserts a tool-declaring step can execute with
only an `adapter_resolver` when the model never actually requests the tool),
only when the model's response actually contains a tool call. A tool call
made without a resolver available fails the step definitively rather than
crashing.

An undeclared tool request and a second tool call in the follow-up response
both fail the step definitively via the existing `fail_step_from_error`
path. Full evidence preservation for the undeclared-tool case (a durable
history entry recording the rejected request) is explicitly #129's scope,
not duplicated here.

`cli.py`'s `run execute-next` previously never passed a `sandbox_resolver`
for provider-message steps at all, which would have made a real (non-worker)
manual dispatch of a tool-declaring step fail even on the happy path; it now
passes the persisted-policy resolver whenever the next step declares tools.
`worker.py`'s `run_worker` already passed both resolvers unconditionally, so
the worker path required no changes beyond the shared runtime logic.

Verification: 9 new chat adapter tests (tool-call parsing, follow-up-turn
mapping, and multi-call rejection across all three adapters), 11 new runtime
tests (end-to-end happy path, phase-ordering-before-execution, env
redaction, undeclared-tool failure, missing-resolver failure, second-call
rejection, and interruption/recovery without re-execution at each phase), 1
new worker test, and 1 new CLI test proving the path from a queued run
through `run execute-next`. Full `pytest` passed 841 (up from 822).
