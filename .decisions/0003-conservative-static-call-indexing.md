# Decision 0003: Index conservative static call relationships

## Status
Accepted

## Context
The structural index records declarations and imports but cannot yet answer which indexed symbols directly invoke other indexed symbols. Before extending the schema, the repository's Python call patterns were surveyed using the standard-library AST across tracked source and tests.

The survey found 594 call sites. Direct-name calls account for 319 sites, simple `self.<name>` calls for 13, and imported-module calls include 24 through `json` plus smaller groups through `os`, `ast`, `hashlib`, and `subprocess`. These patterns provide useful candidates for deterministic local and import-backed resolution. The remaining long tail includes calls through injected transports, protocol-typed adapters, fixture values, arbitrary receivers, chained expressions, and builtins. Those targets cannot be proven from syntax alone.

Representative useful cases include calls from CLI dispatch into `build_clean_index`, `build_incremental_index`, `check_index`, and `explain_symbol`, plus internal `self` method calls in `PythonParser`. Representative unsafe cases include `self._transport(...)`, `adapter.complete(...)`, and methods on values returned by expressions.

## Decision
Add static call relationships in a separately scoped extension, limited to targets supported by explicit syntax and repository declarations:

- Same-module direct-name calls may be `resolved` when exactly one indexed module-level declaration matches.
- `self.<method>` and `cls.<method>` calls may be `resolved` within the lexically enclosing indexed class when exactly one method matches.
- Calls through explicit import aliases may be `resolved` only when the imported target maps uniquely to an indexed repository symbol.
- Syntactic call targets that are useful but not uniquely provable may be emitted as `unresolved`; the index must not guess targets from variable names or runtime types.
- Builtins, third-party APIs, injected callables, arbitrary receiver methods, decorators, and runtime dispatch remain outside resolved repository call edges.

Call records will reuse the existing dependency artifact and evidence vocabulary. The extension must preserve deterministic clean/incremental equivalence and must not add model calls, runtime tracing, or whole-program type inference.

## Consequences
- Agents can use the index for conservative impact analysis across common local calls.
- False certainty is avoided for dependency injection and dynamic Python behavior.
- Some real calls remain unresolved by design, and consumers must continue to inspect source.
- The schema and parser versions will need an explicit compatibility bump when the extension is implemented.
