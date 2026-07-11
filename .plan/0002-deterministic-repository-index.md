# Plan 0002: Deterministic Repository Index

## Status
Planned

## Goal
Give agents and maintainers a reproducible structural map of the repository before the codebase becomes difficult to navigate. The index must be derived entirely from tracked source, safe to rebuild, useful without model calls, and precise about relationships that static analysis cannot prove.

## Initial Scope

The first implementation targets Python and repository metadata already used by codex-agentic-os. It will inventory tracked files and extract Python modules, classes, functions, methods, signatures, imports, and source locations. Cross-language parsing, semantic embeddings, runtime call tracing, and AI-authored summaries are explicitly deferred.

## Determinism Contract

- Identical tracked inputs, indexer version, configuration, and Python minor version produce byte-identical output.
- Generated records use stable keys and lexical ordering; source paths are repository-relative POSIX paths.
- Generated artifacts contain no wall-clock timestamps, host paths, random identifiers, environment-specific values, or secrets.
- The source tree is authoritative. The complete index can always be deleted and rebuilt.
- Relationships are labeled by evidence: `declared`, `resolved`, `inferred`, or `unresolved`. The index must not present dynamic Python behavior as certain.
- Schema and generator versions are recorded separately from source content hashes so compatibility and drift are explicit.

## Proposed Artifacts

```text
.code-index/
├── schema.json
├── manifest.json
├── symbols.jsonl
└── dependencies.jsonl
```

- `schema.json`: versioned field definitions and allowed relationship confidence values.
- `manifest.json`: index version, configuration fingerprint, tracked-file hashes, aggregate content hash, and artifact counts.
- `symbols.jsonl`: one stable record per module, class, function, or method, including qualified name, signature, visibility, path, and line span.
- `dependencies.jsonl`: imports and statically resolvable symbol relationships with evidence labels.

Generated artifacts will be committed so a new session can inspect the repository map immediately. Large-file thresholds and exclusions will be explicit configuration rather than implicit behavior.

## Command Surface

The package CLI should expose:

```text
codex-agentic-os index build
codex-agentic-os index build --incremental
codex-agentic-os index check
codex-agentic-os index explain <qualified-name>
```

- `build` performs a clean deterministic rebuild.
- `build --incremental` re-parses only changed tracked files while producing the same bytes as a clean rebuild.
- `check` rebuilds in a temporary location and fails if committed artifacts differ.
- `explain` provides a human-readable view of one symbol and its indexed relationships without changing files.

## Workflow

1. Developers run the incremental build before committing source changes.
2. A repository-managed pre-commit entry refreshes the index and rejects a commit if regeneration leaves unstaged index changes.
3. CI performs a clean `index check`, proving that the committed incremental result equals a full rebuild.
4. Tests compare clean and incremental builds across fixture repositories and verify byte-for-byte reproducibility.
5. Agents may consume the index for orientation and impact analysis, but must fall back to source inspection when records are unresolved or stale.

The initial workflow must not depend on provider API keys, network access, or an installed Git hook. A documented direct command remains the canonical path; hook integration is an optional convenience around it.

## Tasks

- [ ] Define and test the versioned index schema, stable identifiers, configuration, and determinism rules.
- [ ] Implement tracked-file discovery and content hashing with explicit exclusions and repository-relative paths.
- [ ] Implement Python AST extraction for modules, classes, functions, methods, signatures, imports, and line spans.
- [ ] Implement deterministic manifest, JSONL serialization, atomic writes, and clean rebuilds.
- [ ] Implement incremental rebuilds and prove equivalence with clean rebuild output.
- [ ] Add `index build`, `index check`, and `index explain` CLI commands.
- [ ] Add repository-managed pre-commit integration and contributor documentation.
- [ ] Add CI drift verification using a clean rebuild.
- [ ] Generate and commit the repository's initial `.code-index/` artifacts.
- [ ] Evaluate call/reference indexing on real repository patterns and plan a separate extension if static evidence is useful enough.

## Verification

- Run the indexer twice in clean temporary directories and compare every output byte.
- Touch files without changing content and confirm the output is unchanged.
- Change one symbol and confirm only logically affected records and hashes change.
- Compare incremental and clean rebuild artifacts after adds, edits, renames, and deletes.
- Run with different working-directory paths and confirm host paths never enter output.
- Exercise malformed Python, namespace packages, relative imports, decorators, async functions, nested definitions, and type annotations.
- Confirm `.env`, ignored files, build outputs, virtual environments, and credentials never enter the index.
- Confirm `index check` fails on stale, missing, hand-edited, or schema-incompatible artifacts.

## Deferred Work

- Cross-language parser plugins and language-server integration.
- Runtime traces for dynamic calls, reflection, and dependency injection.
- Semantic embeddings or model-generated summaries.
- Test coverage mapping beyond explicit imports and naming conventions.
- UI graph exploration and run-history overlays.
- Index-backed task routing for multiple agents.

## Resume Notes

Begin with the schema and determinism task, not parsing breadth. Keep the first change small: specify records and golden fixtures before implementing repository discovery. Do not add provider credentials or network dependencies. When this plan becomes active, update the README status and preserve the credential policy already documented there.
