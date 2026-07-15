# Decision 0009: Execution Policy Rules Are a Finite, Closed Criterion Set — Never a Free-Form Expression Language

## Context
Sprint 23 ("Declarative execution policy gates") begins with issue #136:
persist durable execution policy rules that later issues (#137, #138) will
evaluate before a step is claimed and executed. Plan 0122 implemented the
durable model (`ExecutionPolicyRule`/`ExecutionPolicyRegistry` in
`runtime.py`) and its `policy create|list|inspect` CLI. The milestone's gap
statement is explicit: rules must be "built from an explicitly enumerated
finite criteria set; free-form expressions are rejected at creation."

A rule engine is an easy place for scope to creep from "match one durable
attribute" toward "evaluate an operator-supplied boolean expression"
(`network_enabled == true AND tool_name IN (...)`), since that is a more
general and reusable-looking primitive. That generality is also exactly what
makes a policy gate hard to audit, hard to bound for `#137`'s future
before-claim evaluation, and hard to keep out of arbitrary code execution
territory if the expression language ever grows comparison against
attacker-influenced strings.

## Decision
An execution policy rule's condition is exactly one `(criterion_kind,
criterion_value)` pair drawn from a closed, source-enumerated set
(`POLICY_CRITERION_KINDS` in `runtime.py`: `sandbox_network_access`,
`declared_tool_name`, `execution_kind`), each with its own closed value set
or identifier-shape validation (`enabled`/`disabled`; a valid Python
identifier; `command`/`provider`/`delegation`). There is no field, flag, or
CLI argument through which a caller can supply a compound, boolean, or
otherwise free-form expression — `ExecutionPolicyRegistry.create_rule`'s
signature has no "expression" parameter at all, and unknown criterion kinds
are rejected both by `argparse`'s `choices=` at the CLI layer and again by
`ExecutionPolicyRegistry._validate_criterion_kind` at the registry layer
before any durable mutation. `criterion_value` is further rejected if it
carries leading/trailing whitespace, so an operator cannot smuggle
expression-like content (`"command == provider"`) past the closed value-set
check by relying on whitespace tolerance.

Extending the criterion set to a new durable step attribute in a future
sprint is additive (append to `POLICY_CRITERION_KINDS` and add one value
validator branch) and remains within this decision. Introducing any
combinator (AND/OR/NOT), wildcard matching, or an operator-authored
expression string is not additive — it changes the shape of what a rule is
and requires revisiting this decision.

## Rationale
A finite criterion set is exhaustively enumerable, so every possible rule
that can ever be persisted is knowable from reading
`POLICY_CRITERION_KINDS` and its three value validators — there is no
parser, no operator precedence, and no injection surface. This keeps
`#137`'s future before-claim evaluation a bounded, total function over a
known step attribute (sandbox network policy, declared tool name, execution
kind) rather than an interpreter that must itself be secured, tested for
combinatorial edge cases, and kept from evaluating attacker-influenced
step-authored strings. It also matches how every other durable criterion in
this codebase is expressed: `_validate_tool_declarations` already requires
tool names to be valid identifiers rather than arbitrary strings, and
`_execution_kind` already returns one of exactly three fixed values — this
decision keeps policy rules consistent with, not more expressive than, the
attributes they match against.

## Consequences
- Any future issue that wants to gate on a step attribute this set does not
  yet cover (for example a delegation target, an artifact declaration, or a
  capability) must add a new named criterion kind and its own strict value
  validator to `runtime.py`, not a generic "attribute path" or "expression"
  escape hatch.
- `#137` ("Apply execution policy rules before step claim") must evaluate
  rules as an exact match against one durable step attribute per rule, not
  as an expression interpreter; if boolean combination across multiple
  criteria is ever needed, that is multiple rules composed by the evaluator,
  not one rule with a richer condition language.
- A CLI or API surface that reports "unknown criterion kind" or "malformed
  value" must keep failing before any durable mutation (verified by the
  `..._without_mutation` tests in `tests/test_runtime.py` and
  `tests/test_policy_cli.py`); this is a durable safety property, not
  incidental to the current implementation.
