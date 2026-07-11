# codex-agentic-os

codex-agentic-os is an agentic operating system project: a durable home for agents that can plan, execute, sandbox work, remember decisions, and route model calls across multiple providers.

## Direction

This repository is being maintained as an incremental, scheduled build. Each run should tackle one focused plan item, update the project record, and leave the repo in a resumable state.

## First-class model providers

The OS is intentionally provider-neutral. The foundation declares support for:

- OpenAI
- Anthropic
- Google
- OpenRouter
- LM Studio
- Ollama
- OpenAI-compatible endpoints

Provider declarations live in `src/codex_agentic_os/providers.py`. Concrete adapters will be added behind this interface as the runtime matures.

## Agent runtime strategy

The initial runtime is a small internal core with typed boundaries. This avoids overfitting too early while leaving room to add adapters for orchestration frameworks such as LangChain DeepAgents when a plan calls for them.

Runtime declarations live in `src/codex_agentic_os/runtime.py`.

## Sandbox execution

Sandboxing is a required capability. The first supported container backends are:

- Docker
- Podman

Sandbox declarations live in `src/codex_agentic_os/sandboxes.py`. Execution adapters are planned next.

## Planning and decisions

- `.plan/` stores active and historical implementation plans.
- `.decisions/` stores architectural decision records explaining why choices were made.
- `.github/workflows/hourly-agentic-os.yml` defines an hourly heartbeat workflow that identifies the next unchecked plan task.

## Current status

Implemented foundation:

- Python package metadata and CLI entrypoint.
- Provider family declarations and tests.
- Docker/Podman sandbox declarations and tests.
- Plan and decision records.
- Hourly GitHub Actions heartbeat.
- Provider-neutral chat request/response types and an injectable OpenAI-compatible adapter.
- Native Anthropic Messages API adapter with system-message normalization and prompt caching.
- Native Google `models.generateContent` adapter with provider-neutral role and generation mapping.
- Language-neutral repository-index schema, parser interface, stable identifiers, configuration fingerprints, and deterministic JSON/JSONL encoding.
- Git-backed tracked-file discovery with explicit include/exclude rules, size limits, repository-relative paths, and deterministic SHA-256 worktree hashes.
- Deterministic Python AST extraction for modules, classes, functions, methods, signatures, imports, and source spans.
- Deterministic clean repository-index builds with versioned manifests, JSONL artifacts, atomic file replacement, and stale-output cleanup.
- Incremental repository-index builds that reparse only changed tracked files and remain byte-identical to clean builds across additions, edits, renames, and deletions.
- Repository-index CLI commands for clean or incremental builds, read-only drift checks, and symbol explanations.
- Optional repository-managed pre-commit refresh that rejects unstaged generated index changes.
- Pull request and `main` branch CI that runs tests and rejects repository-index drift using a clean rebuild.
- Committed initial `.code-index/` artifacts for immediate repository orientation and CI drift enforcement.
- Completed a repository-wide static-call evaluation and accepted a conservative call-reference extension with explicit evidence limits.
- Versioned the static call-relationship contract with stable enclosing-symbol source IDs and explicit resolved/unresolved target identity rules.
- Added deterministic Python call-candidate extraction with lexically enclosing function and method identities.
- Added conservative resolution for unique same-module, lexical `self`/`cls`, and explicit repository import-alias calls.
- Preserved useful unresolved dynamic calls while filtering direct builtins and explicit non-repository import calls.
- Proved byte-identical clean and incremental index output across call additions, edits, target renames, and deletions.

Verification note: the full local pytest suite passes.

Planned next:

1. Continue the conservative static call-reference extension in `.plan/0003-static-call-reference-index.md` by surfacing incoming and outgoing calls through `index explain` and documenting evidence limitations.
2. Docker and Podman sandbox execution adapters.
3. Persistent state for agent runs, plans, and decisions.

## Development

### Provider credentials

Provider integrations must remain independently usable: a missing API key for one provider must not block development or tests for other providers. Prefer injected transports and offline tests; require live credentials only for explicit integration or end-to-end verification.

When a task first requires environment-based configuration, add or update a committed `.env.example` containing placeholder values and ensure `.env` is ignored by Git. Do not commit secrets. The session performing that work must tell the user to copy `.env.example` to `.env`, identify which variables are required or optional, and clearly report any verification skipped because a key is unavailable.

Install locally:

```bash
python -m pip install -e '.[dev]'
```

Run tests:

```bash
pytest
```

Inspect declared capabilities:

```bash
codex-agentic-os
```

Build and inspect the deterministic repository index from a repository root:

```bash
codex-agentic-os index build
codex-agentic-os index build --incremental
codex-agentic-os index check
codex-agentic-os index explain codex_agentic_os.index.build_clean_index
```

`index check` performs a clean rebuild in a temporary directory and returns a nonzero exit status if committed artifacts are missing or stale. `index explain` reads the existing index without changing it.

CI runs the full test suite followed by `index check` for pull requests and pushes to `main`. This clean rebuild is the drift gate for committed `.code-index/` artifacts.

### Pre-commit index refresh

After installing the development dependencies, contributors may install the repository-managed hook:

```bash
pre-commit install
```

On every commit, the hook runs an incremental index build. If regeneration changes `.code-index/`, the commit is stopped so the refreshed artifacts can be reviewed and staged:

```bash
git add .code-index
git commit
```

The hook is optional. `codex-agentic-os index build --incremental` remains the canonical direct command, and `codex-agentic-os index check` provides a read-only clean-build verification.
