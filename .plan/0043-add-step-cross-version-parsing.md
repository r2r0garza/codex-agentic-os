# Plan 0043: Cross-version `run add-step` command parsing

## Status
Complete

## Goal
Make `run add-step` accept both command-bearing and coordination-only steps
identically on Python 3.11 and Python 3.12, restoring green CI.

## Tasks
- [x] Remove the trailing `step_command` `nargs="*"` positional, whose
      interleaving with preceding options (`--objective`, `--timeout`,
      `--state-db`) argparse resolves inconsistently between Python 3.11 and
      3.12.
- [x] Parse `run add-step` with `parser.parse_known_args()` and assemble the
      trailing command from the leftover tokens, stripping a single leading
      `--` separator to match the prior positional's behavior.
- [x] Preserve strict `unrecognized arguments` rejection for every other
      subcommand by erroring on any leftover tokens outside `add-step`.
- [x] Add regression tests for a hyphen-prefixed command after `--`, a bare
      trailing `--` (coordination-only), and unrecognized arguments on a
      non-`add-step` command.

## Resume Notes
Issue #37 is complete. Root cause: passing `--state-db PATH COMMAND ARGS` (no
explicit `--`) to a subparser with a trailing `nargs="*"` positional and
preceding options is parsed successfully by Python 3.12's argparse but
rejected as `unrecognized arguments` by Python 3.11's — confirmed with a
minimal reproduction under both interpreters. `parse_known_args()` plus manual
leftover-token handling was consistent across both versions in every tested
case (plain trailing command, explicit `--`, hyphen-prefixed arguments after
`--`, bare `--`, and full omission). See [[0007-cross-version-argparse-trailing-command]]
for the general parsing decision. Verified with `pytest -q tests/test_run_cli.py`
and the full suite under Python 3.11.15 and 3.12.13, plus an incremental index
rebuild and `index check`.
