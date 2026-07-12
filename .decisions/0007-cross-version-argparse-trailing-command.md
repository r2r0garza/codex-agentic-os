# Decision 0007: Manual leftover-token parsing for trailing CLI commands

## Status
Accepted

## Context
`run add-step` took an optional trailing command as a `nargs="*"` positional
declared alongside preceding options (`--objective`, `--timeout`,
`--state-db`). Python 3.11's argparse rejects a plain trailing command in
this shape as `unrecognized arguments`, while Python 3.12's accepts it;
`argparse`'s optional/positional interleaving algorithm differs across these
versions and reordering `add_argument()` calls does not change the outcome.

## Decision
Do not declare an optional trailing command as an argparse positional
alongside other options. Instead, parse the subcommand with
`parser.parse_known_args()` and manually treat the leftover tokens as the
trailing command, stripping one leading `--` if present. For every other
subcommand, treat any leftover token as a hard `unrecognized arguments`
error, matching argparse's own default rejection.

## Consequences
- `run add-step`'s trailing command behaves identically on Python 3.11 and
  3.12 for a plain trailing command, an explicit `--` separator, hyphen-
  prefixed command arguments after `--`, a bare trailing `--`, and omission.
- Any future subcommand that wants an optional trailing command after other
  options should use this same leftover-token pattern rather than a trailing
  `nargs="*"` positional, to avoid reintroducing a version-dependent parse.
- `add-step --help` no longer shows the trailing command as a generated
  positional; its usage and epilog are set explicitly to document it instead.
