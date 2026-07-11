# Plan 0003: Conservative Static Call Reference Index

## Status
Complete

## Goal
Extend the deterministic repository index with useful Python call relationships while preserving explicit evidence boundaries for dynamic behavior.

## Scope
Index syntactic calls originating inside indexed functions and methods. Resolve only unique same-module direct names, lexical `self`/`cls` methods, and explicit import aliases that point to repository symbols. Record useful ambiguous targets as unresolved without guessing from runtime types. Keep call relationships in `dependencies.jsonl` and preserve all existing determinism and incremental-build guarantees.

## Tasks

- [x] Define the versioned call relationship contract, stable source identity, and schema/parser compatibility bumps.
- [x] Extract deterministic call candidates with enclosing-symbol context from Python ASTs.
- [x] Resolve unique same-module, lexical `self`/`cls`, and repository import-alias targets with explicit evidence labels.
- [x] Preserve unresolved dynamic call evidence without indexing builtins or third-party receiver methods as repository targets.
- [x] Prove clean and incremental byte equivalence for call additions, edits, renames, and deletions.
- [x] Surface incoming and outgoing call relationships through `index explain` and document the evidence limitations.
- [x] Regenerate committed artifacts and verify CI drift enforcement.

## Verification

- Cover direct functions, nested functions, methods, async calls, aliases, relative imports, and duplicate names.
- Cover injected callables, protocol receivers, arbitrary attributes, chained expressions, decorators, and builtins as unresolved or excluded according to the contract.
- Assert every resolved target exists in `symbols.jsonl` and every call source is its enclosing indexed symbol.
- Assert lexical ordering, stable identifiers, host-path exclusion, and byte-identical repeated builds.
- Compare incremental and clean artifacts after call-site and target changes.

## Resume Notes

The conservative static call-reference extension is complete. The committed `.code-index/` artifacts now contain 147 symbols and 613 relationships, `index check` accepts the regenerated output, and the full 36-test suite covers the CI drift gate, including stale or missing artifacts. Resume from Plan 0001 with sandbox command execution for Docker and Podman as the next unchecked task; keep that implementation focused and preserve the provider-neutral boundaries.
