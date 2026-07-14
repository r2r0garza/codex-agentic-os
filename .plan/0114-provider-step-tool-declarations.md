# Plan 0114: Provider-Step Tool Declarations

## Status
Complete

## Goal
Let a provider-message step declare a bounded allowlist of named tools, each
bound to a persisted sandboxed command template under the step's own durable
sandbox policy, without changing behavior for provider steps that declare no
tools.

## Tasks
- [x] Add a durable `ToolDeclaration` model (name, command template,
      optional description, optional parameters schema) and a `RunStep.
      tool_declarations` field.
- [x] Validate declarations before mutation: unique identifier-safe names,
      non-empty command templates, provider-message-only scope, and a
      required persisted sandbox policy.
- [x] Allow a persisted `sandbox_policy` on provider-message steps that
      declare tools (previously command-step-only), preserving the existing
      rejection for provider steps without tools.
- [x] Preserve tool declarations (and, for provider steps, sandbox policy)
      across every existing step-payload rewrite path that already preserves
      provider messages: cancel closure, `start_next_step`, `transition_step`,
      `_decision_payload`, `complete_step_from_result`,
      `complete_step_from_chat_response`, `fail_step_from_error`, and
      `recover_running_step`.
- [x] Extend `run add-step` with a repeatable `--tool JSON` flag and extend
      `run inspect`/`run inspect-step` output; preserve the no-tool payload
      shape exactly.
- [x] Add focused runtime and CLI tests; run the full suite; refresh the
      index.

## Resume Notes
Selected active-milestone issue: #126 (Sprint 21 "Durable model tool
calling", priority:1, `agent-ready`), the sole unblocked issue; #127/#128/#129
remain correctly `blocked` on it and on each other.

`SandboxPolicy` was previously rejected outright for any step without a
command (`_validate_sandbox_policy(..., has_command=...)`). Since tool
execution in a later slice (#128) must run through the same sandbox
boundary, this slice widens that guard with a `has_tools` flag computed from
the raw (pre-normalization) `tools` argument, so a provider step may now
persist a sandbox policy specifically when it declares tools; provider steps
without tools still reject a sandbox policy with the same error text
substring existing tests already assert on.

`complete_step_from_chat_response`'s step-payload rewrite never referenced
`sandbox_policy` before, because a provider step could not have one. Adding
tool-declaring provider steps means this terminal-completion path would have
silently dropped a persisted sandbox policy and its tool declarations on a
successful response; both are now preserved there alongside the unchanged
message/output/artifact-name fields.

Tool declaration validation lives in `_validate_tool_declarations` next to
`_validate_sandbox_policy`, following the existing `_validate_artifact_
declarations` pattern of taking the already-normalized `sandbox_policy` as
a parameter so declaring tools without one fails before any mutation. Tool
names reuse the `str.isidentifier()` convention already used for sandbox
`env_passthrough` names, since #127's adapter mapping will need
provider-safe identifiers. `parameters` is stored as an opaque JSON object
(no adapter-specific schema validation here) and round-trips through the
existing `json.dumps`/`json.loads` state layer unchanged.

`PlanStepProposal` (model-proposed plan steps) intentionally does not gain a
tools field; the issue scopes tool declaration to `add_step`/CLI, and no
milestone exit criterion asks a proposed plan step to declare tools.

Verification: 20 new focused tests (13 runtime, 7 CLI) covering persistence
across restart, no-tool backward compatibility, sandbox-policy/provider-scope
rejection, invalid-declaration rejection (name, command, description,
parameters, unknown fields), CLI create/inspect parity, and CLI rejection
paths without mutation, plus preservation across a provider-step failure and
reload. Full `pytest` passed 815 (up from 793); refreshed index; `index
check` and `git diff --check` passed.
