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

## Persistent runtime state

Plans, decisions, runs, and agent state can be stored as versioned JSON documents in a
local SQLite database through `StateStore`. Runtime databases should live under the
ignored `.codex-agentic-os/` directory; planning and architectural Markdown records
remain committed source-of-truth documentation.

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
- Docker/Podman sandbox declarations plus shell-free command execution with isolated defaults and captured results.
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
- Surfaced conservative incoming and outgoing static calls through `index explain`.
- Regenerated and committed the call-aware repository index, with CI clean-rebuild drift enforcement verified.
- Durable SQLite persistence for plans, decisions, runs, and agent state, including revision tracking and deterministic reads.
- Typed durable run coordination with validated queued, running, terminal, and cancellation transitions.
- Durable position-ordered run steps with validated lifecycle transitions, revision tracking, and terminal output.
- Backend-neutral sandbox-result recording that completes durable steps and automatically succeeds or fails their runs.

Verification note: the full local pytest suite passes.

Planned next: expose read-only run and ordered-step inspection through the CLI, as scoped by Plan 0004.

## Development

### Frontend stack and scaffolding

When frontend work begins, use Next.js, React, and shadcn/ui components. Create the frontend as a named child directory from the directory that should contain it. For example, run the scaffold from the repository root when the frontend folder should live beside `src/`:

```bash
pnpm dlx shadcn@latest init --preset b0 --template next --name [folder-name] -y
```

The scaffold contains its own `.gitignore` and initialized `.git/` directory. Before continuing:

1. Merge the scaffold's ignore rules into the repository-root `.gitignore`.
2. Delete only the scaffold's nested `.git/` directory so the frontend remains part of this repository rather than becoming a nested repository.
3. Change into the generated frontend directory.

Install the complete shadcn component set from inside the generated frontend directory:

```bash
pnpm dlx shadcn@latest add accordion alert alert-dialog aspect-ratio attachment avatar badge breadcrumb bubble button button-group calendar card carousel chart checkbox collapsible combobox command context-menu table dialog drawer dropdown-menu empty field hover-card input input-group input-otp item kbd label marker menubar message message-scroller native-select navigation-menu pagination popover progress radio-group resizable scroll-area select separator sheet sidebar skeleton slider sonner spinner switch tabs textarea toggle toggle-group tooltip
pnpm add @tanstack/react-table
```

Use the installed `Popover` and `Calendar` components together for date pickers. Use Sonner for toast notifications.

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

Persist runtime state:

```python
from codex_agentic_os import StateStore

store = StateStore(".codex-agentic-os/state.sqlite3")
store.put("run", "run-001", status="running", payload={"plan": "plan-001"})
```

Coordinate a validated durable run lifecycle:

```python
from codex_agentic_os import RunCoordinator, RunStatus, StateStore

runs = RunCoordinator(StateStore(".codex-agentic-os/state.sqlite3"))
runs.create("run-001", objective="Build the repository index")
runs.transition("run-001", RunStatus.RUNNING)
runs.transition("run-001", RunStatus.SUCCEEDED, output={"artifacts": 4})
```

Append and coordinate ordered durable steps independently of an execution backend:

```python
from codex_agentic_os import RunCoordinator, StepStatus, StateStore

runs = RunCoordinator(StateStore(".codex-agentic-os/state.sqlite3"))
runs.create("run-002", objective="Execute a sandboxed task")
runs.add_step("run-002", "step-001", objective="Run the command")
runs.transition_step("step-001", StepStatus.RUNNING)
runs.transition_step("step-001", StepStatus.SUCCEEDED, output={"exit_code": 0})
```

Record a sandbox result through the structural execution-result boundary. A zero exit
completes the step successfully and succeeds the run when every step is complete; a
nonzero exit fails both the step and run:

```python
step, run = runs.complete_step_from_result("step-001", result)
```

Inspect declared capabilities:

```bash
codex-agentic-os
```

Execute a command through either supported container engine from Python:

```python
from codex_agentic_os import ContainerSandbox, SandboxKind, SandboxSpec

sandbox = ContainerSandbox(SandboxSpec(kind=SandboxKind.DOCKER))
result = sandbox.execute(("python", "-c", "print('hello')"), timeout=30)
```

The default container run disables networking, uses a read-only root filesystem, limits CPU and memory, removes the container after execution, and captures stdout, stderr, and the exit code. Override those settings explicitly on `SandboxSpec` when a task requires different capabilities. Docker or Podman must be installed for live execution; unit tests use an injected process runner and do not require either engine.

Build and inspect the deterministic repository index from a repository root:

```bash
codex-agentic-os index build
codex-agentic-os index build --incremental
codex-agentic-os index check
codex-agentic-os index explain codex_agentic_os.index.build_clean_index
```

`index check` performs a clean rebuild in a temporary directory and returns a nonzero exit status if committed artifacts are missing or stale. `index explain` reads the existing index without changing it.

The `index explain` payload keeps all source-owned entries in `relationships` and also exposes `outgoing_calls` and `incoming_calls`. Outgoing calls include unresolved syntactic candidates; incoming calls include only resolved edges whose `target_id` proves the indexed target. Dynamic dispatch, injected callables, arbitrary receiver methods, builtins, and external APIs are not inferred as incoming repository calls, so impact analysis must still inspect source when evidence is absent or unresolved.

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
