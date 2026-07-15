# Plan 0122: Persist Finite Declarative Execution Policy Rules

## Status
Complete

## Goal
Add a durable, finite-criterion execution policy rule model with stable
identity, precedence, enabled state, and an operator-readable reason; reject
unknown criterion kinds, malformed values, duplicate identities, and any
free-form expression syntax before mutation; expose the rules through
read-only, credential-free CLI listing/inspection without evaluating them.

## Tasks
- [x] Add `policy_rule` to `StateStore.KINDS` and widen the `state_records`
      `kind` CHECK constraint, following the existing rename/recreate/copy
      migration used when `step`/`artifact` were added.
- [x] Add `ExecutionPolicyRule` (rule id, enabled, precedence, criterion
      kind, criterion value, reason, created-at) and `ExecutionPolicyRegistry`
      to `runtime.py`, mirroring `Agent`/`AgentRegistry`.
- [x] Enumerate the finite criterion-kind set
      (`POLICY_CRITERION_KINDS = {sandbox_network_access, declared_tool_name,
      execution_kind}`) and validate each kind's value against its own
      strict, closed value set or identifier shape, so no free-form
      expression syntax can reach durable state.
- [x] Validate rule id, reason, precedence, and enabled state before any
      mutation; translate a duplicate rule id into a domain `ValueError` the
      same way `AgentRegistry.register` does.
- [x] Add a `policy create|list|inspect` CLI command family with its own
      `--state-db` flag and a `read_only` `StateStore` for `list`/`inspect`,
      matching the `agent` command's wiring exactly; no `edit`/`delete`
      subcommand, per scope.
- [x] Add focused runtime and CLI tests: durable creation, fresh-process
      reload, stable listing order, every finite criterion accepted,
      duplicate/malformed/empty rejection without mutation, and a corrupted
      legacy record raising on read.
- [x] Refresh the index and verify the full suite.

## Resume Notes
Selected active-milestone issue: #136 (Sprint 23 "Declarative execution
policy gates", priority:1, `agent-ready`), the sole unblocked issue; #137
("Apply execution policy rules before step claim") and #138 ("Review
policy-gated network execution end to end") remain correctly `blocked` on it.

Rule evaluation, automatic approval marking, policy simulation, rule
editing/deletion, and role-based access control are explicitly out of scope
for this issue and were not implemented; #137 is expected to consume this
registry's `list_rules()` to gate step claims.

The finite criterion set was chosen to mirror durable step attributes that
already exist and are inspectable: `sandbox_policy.network_enabled`
(`enabled`/`disabled`), the tool-declaration `name` identifier used by
`_validate_tool_declarations`, and `RunStep`'s three `_execution_kind`
values (`command`, `provider`, `delegation`). Rejecting free-form expression
syntax is enforced structurally rather than by pattern-matching a string:
`create_rule` only accepts a single `(criterion_kind, criterion_value)` pair
from this closed set, so there is no field through which a boolean or
compound expression could be persisted. `criterion_value` is additionally
rejected if it carries leading/trailing whitespace, closing the obvious
"expression disguised as one string" gap (for example
`"command == provider"` is rejected by `execution_kind`'s closed value set
regardless).

Precedence is a required, caller-supplied non-negative integer rather than
an implicit creation-order counter: `StateStore.list` returns rows in
key/identifier order, not insertion order, so a durable, explicit precedence
field is the only way to give a future evaluator (#137) a stable ordering
signal independent of how rule ids happen to sort.

`payload.get("enabled")`/`"precedence"`/etc. are re-validated on every read
in `ExecutionPolicyRegistry._rule`, matching `AgentRegistry._agent`'s
defensive-read pattern; a corrupted or hand-inserted durable record with an
invalid shape raises `ValueError` on `get`/`list_rules` rather than silently
returning malformed data.

Verification: 29 new focused runtime tests and 15 new focused CLI tests
covering durable creation, fresh-process reload, every finite criterion,
stable listing order, duplicate/malformed/empty rejection without mutation,
and a corrupted legacy record raising on read. Full `pytest` suite passed.
Index rebuilt and `index check` reported current; `git diff --check` passed.
