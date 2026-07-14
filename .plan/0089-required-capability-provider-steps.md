# Plan 0089: Required-Capability Provider Steps and Validation

## Status
Complete

## Goal
Let a provider-message step declare a required capability instead of a fixed
provider, reject steps that declare both or a capability no default provider
spec declares, and expose declared capabilities on provider listing —
closing Sprint 14 issue #93 without touching dispatch-time resolution
(Sprint 14 issue #94's scope).

## Tasks
- [x] Add a `capabilities: tuple[str, ...] = ()` field to `ProviderSpec`
      (`src/codex_agentic_os/providers.py`) and declare capabilities on each
      `DEFAULT_PROVIDER_SPECS` entry; `OPENAI_COMPATIBLE` declares none since
      it is an unconfigured custom endpoint.
- [x] Make `ProviderMessage.provider` optional and add
      `ProviderMessage.required_capability`, mutually exclusive with
      `provider` (`src/codex_agentic_os/runtime.py`).
- [x] Extend `RunCoordinator._validate_message` to require exactly one of
      `provider`/`required_capability`, and to reject an unknown
      `required_capability` against the module-level
      `_KNOWN_PROVIDER_CAPABILITIES` set (derived from
      `DEFAULT_PROVIDER_SPECS`) before any state mutation. `add_step`,
      `_step` (restart reconstruction), and `_parse_plan_proposal`/
      `_plan_step_proposal` (plan drafts) all route through this one
      validator, so the rule applies uniformly.
- [x] Update `PLAN_PROPOSAL_SYSTEM_PROMPT` to document the mutually
      exclusive `provider`/`required_capability` message shape.
- [x] Add a `--capability` CLI flag to `run add-step`, mutually exclusive
      with `--provider` (enforced by the shared validator, not argparse).
- [x] Update DEVELOPMENT.md's `add-step` and plan-proposal shape sections.
- [x] Add focused tests: `ProviderSpec` capability declarations and
      provider-list JSON shape/values; runtime `add_step` round-trip,
      both-fields rejection, neither-field rejection, and unknown-capability
      rejection (all pre-mutation); a fixed-provider regression check; plan
      proposal parsing/materialization for a capability-routed step plus
      malformed-proposal cases (both fields, neither field, unknown
      capability); CLI add-step positive/negative cases; updated the two
      existing exact-payload fixtures (`test_cli_adds_and_inspects_provider_message_step`,
      `PLAN_PROPOSAL_STEPS_PAYLOAD`) for the new additive `required_capability`
      field.
- [x] Rebuild/check the index, run the full suite, and run `git diff --check`.

## Resume Notes
Selected active-milestone issue: #93 (priority:1, sole `agent-ready` issue in
Sprint 14 "Capability-based provider routing" at run start; #94 and #95 are
`blocked` on it).

`ProviderSpec.to_dict()` already recursively serializes new dataclass fields,
so `provider list` exposes `capabilities` with no CLI change — DEVELOPMENT.md
already documented "declared capabilities" in its `provider list` section
ahead of the implementation, which this issue makes true.

Field ordering matters: `ProviderMessage.provider` kept its original
position (first field) with its type changed to `str | None` and no default,
so the many existing positional two-arg call sites in tests
(`ProviderMessage("local", "Use it")`) keep meaning `provider="local",
content="Use it"` unchanged. `required_capability` was appended as a new
trailing field with a default, which is dataclass-ordering-safe and leaves
every existing keyword-argument call site unaffected.

Two independent JSON payload builders exist for a provider message: the
persistence-facing `RunCoordinator._message_payload`/`_plan_step_proposal_payload`
in runtime.py filter out `None` fields (storage stays compact and
backward-compatible on disk), while the CLI-facing `_step_payload`/
`_plan_step_proposal_payload` in cli.py use unfiltered `asdict()` (matching
the existing convention for both). Adding `required_capability` therefore
makes it always appear (as `null` when unset) in CLI JSON output for every
provider-message step, command, or plan step — an additive, documented
field per the issue's acceptance criteria, not a value that disappears based
on which path is set. Updated the exact-shape fixtures that assumed six
message fields to expect seven.

Dispatch-time resolution is out of scope (#94): a capability-only step
reaching `execute_next_step` today calls `_provider_adapter_resolver`'s
`_chat_provider_spec(message.provider, ...)` with `provider=None`, which
raises inside `ProviderKind(None)` before any spec lookup. That `ValueError`
is already caught by `execute_next_step`'s existing `except (ValueError,
RuntimeError, NotImplementedError)` clause and recorded as a definite step
failure — no state corruption, no new exception path needed. Left
unexercised by a new test since exercising dispatch behavior for
capability-routed steps is #94's responsibility.

Full suite `594 passed` (up from 579, +15 net new/updated tests). Rebuilt
index (886 symbols, 5160 relationships, was stale after the source changes)
and confirmed current. `git diff --check` clean.
