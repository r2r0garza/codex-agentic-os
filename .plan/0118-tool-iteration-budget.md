# Plan 0118: Explicit Tool-Iteration Budget

## Status
Complete

## Goal
Require every tool-declaring provider step to persist an explicit maximum
tool-iteration budget at creation, reject a missing or invalid budget before
mutation, and expose the budget through trusted inspection without changing
no-tool step behavior.

## Tasks
- [x] Add a durable `RunStep.tool_iteration_budget` field alongside
      `tool_declarations`.
- [x] Validate the budget before mutation: it must be explicit, a positive
      integer, and present exactly when tools are declared; reject it for
      steps without tools with the same error shape as `sandbox_policy`.
- [x] Preserve the budget across every existing step-payload rewrite path
      that already preserves `tool_declarations`: cancel closure,
      `start_next_step`, `_running_provider_step_payload` (both tool-call
      phase writes), `transition_step`, `complete_step_from_result`,
      `complete_step_from_chat_response`, `fail_step_from_error`,
      `recover_running_step`, `_decision_payload`, and retry copying.
- [x] Extend `run add-step` with `--tool-iteration-budget COUNT` and extend
      `run inspect`/`run inspect-step` output; preserve the no-tool payload
      shape exactly.
- [x] Allow a durable record persisted before this budget existed (tools
      declared, no budget) to still load for trusted inspection.
- [x] Add focused runtime and CLI tests; run the full suite; refresh the
      index.

## Resume Notes
Selected active-milestone issue: #131 (Sprint 22 "Bounded agentic tool
loop", priority:1, `agent-ready`), the sole unblocked issue; #132/#133/#134
remain correctly `blocked` on it and on each other.

The budget's required-ness is deliberately asymmetric between the create and
read paths. `_validate_tool_iteration_budget` takes a `require_explicit`
flag: `add_step` calls it with `require_explicit=True` so creating a new
tool-declaring step without a budget fails before any mutation (the
milestone's "no default unlimited loop" contract), while `_step()` (the
record-parsing path used by every read and every rewrite's final
re-validation) calls it with `require_explicit=False` so a step persisted
before this field existed still loads with `tool_iteration_budget=None`
instead of failing closed. A present-but-invalid value (non-integer, zero,
negative, or a bool) is rejected on both paths; only a missing value is
tolerated on read.

Every rewrite path that already preserves `tool_declarations` follows the
identical `if step.tool_declarations: payload["tools"] = ...` shape, so the
budget is preserved by adding one line inside each of those existing
conditionals rather than introducing new branches — the budget's presence is
therefore structurally tied to the declarations it bounds. The one path that
builds a step payload from a raw command result
(`complete_step_from_result`) can never actually have tool declarations
(tools are provider-message-only), so its added line is defensive symmetry,
not reachable behavior change. `retry_failed_step` in `state.py` needed its
own preserved-key list extended with `"tool_iteration_budget"` since it
copies the raw stored payload directly rather than going through any of the
`RunCoordinator` payload builders.

`payloads.py`'s `_step_payload` (shared by the CLI and the HTTP API) needed
no redaction change: the budget is a plain positive integer, not sensitive
input or captured output, so it stays visible over loopback HTTP exactly
like a sandbox policy's `kind` does.

Several existing tests from Plans 0114/0116/0117 created tool-declaring
steps via `add_step(tools=[...])` without a budget; those now fail
`add_step`'s new required-budget validation and were updated to pass an
explicit `tool_iteration_budget`. Tests that assert rejection *before*
reaching budget validation (missing sandbox policy, command-step scope,
malformed tool declarations) were left unchanged since that validation order
is unaffected — declarations are validated before the budget.

Verification: 9 new focused runtime tests (missing-budget rejection,
invalid-value rejection across zero/negative/float/bool/string, budget
rejected without tools, retry preservation, and legacy no-budget record
read-back), 2 updated CLI tests exercising `--tool-iteration-budget` end to
end through `run add-step`/`run inspect-step`/`run execute-next`, plus
existing tool-declaration and worker tests updated to supply the now-required
budget. Full `pytest` passed 852 (up from 843). Rebuilt index to 27 files /
1276 symbols / 7555 relationships; `index check` reported current; `git diff
--check` passed.
