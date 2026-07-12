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
- Read-only CLI inspection of durable runs and their position-ordered steps.
- Coordinated durable run cancellation that cancels active steps while preserving completed step history.
- Atomic run cancellation that rolls back all run and step updates on persistence failure.
- Atomic terminal sandbox-result completion that keeps step and run lifecycle state consistent.
- Atomic running-step recovery that keeps failed step and run lifecycle state consistent.
- Operator-facing `run cancel` CLI support with persisted, position-ordered JSON confirmation.
- Atomic backend-neutral next-step dispatch in durable position order with single-active-step enforcement.
- Durable optional command arguments and timeouts on ordered run steps.
- Injected sandbox execution of the next durable command step with persisted results.
- Explicit durable recovery for interrupted or timed-out running steps.
- Operator-facing `run recover` CLI support with typed reasons and optional detail.
- Read-only deterministic listing of durable runs through `run list`.
- Operator-facing queued run creation through `run create`.

Verification note: the full local pytest suite passes.

Planned next: choose the next prioritized `agent-ready` issue; Plan 0016 is complete.

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

Create a queued durable run from the CLI. The state database is created when needed,
and the resulting run is printed with an empty ordered step list:

```bash
codex-agentic-os run create run-001 --objective "Build the repository index"
codex-agentic-os run create run-002 --objective "Execute durable work" \
  --agent-id agent-1 --state-db /path/to/state.sqlite3
```

Coordinate a validated durable run lifecycle:

```python
from codex_agentic_os import RunCoordinator, RunStatus, StateStore

runs = RunCoordinator(StateStore(".codex-agentic-os/state.sqlite3"))
runs.create("run-001", objective="Build the repository index")
runs.transition("run-001", RunStatus.RUNNING)
runs.transition("run-001", RunStatus.SUCCEEDED, output={"artifacts": 4})
```

Cancel a queued or running run consistently with its active steps. Succeeded, failed,
or already-cancelled steps retain their terminal status and output:

```python
cancelled = runs.cancel("run-001")
```

Append and coordinate ordered durable steps independently of an execution backend:

```python
from codex_agentic_os import RunCoordinator, StepStatus, StateStore

runs = RunCoordinator(StateStore(".codex-agentic-os/state.sqlite3"))
runs.create("run-002", objective="Execute a sandboxed task")
runs.add_step(
    "run-002",
    "step-001",
    objective="Run the command",
    command=("python", "-c", "print('hello')"),
    timeout=30,
)
step = runs.start_next_step("run-002")
runs.transition_step("step-001", StepStatus.SUCCEEDED, output={"exit_code": 0})
```

`start_next_step()` starts the earliest queued step and moves a queued run to running.
It rejects dispatch when the run already has a running step, preserving sequential
execution without coupling coordination to a sandbox backend.

Execute the earliest queued command step through any injected executor that implements
the sandbox execution boundary:

```python
step, run = runs.execute_next_step("run-002", sandbox)
```

The stored command and timeout are passed to the executor, and its result completes the
step and updates the run. Coordination-only steps are rejected before mutation. If the
executor raises before returning a result, the run and step remain running for explicit
recovery. Reconcile that uncertain execution explicitly without retrying it:

```python
from codex_agentic_os import StepRecoveryReason

step, run = runs.recover_running_step(
    "step-001",
    StepRecoveryReason.TIMED_OUT,
    detail="worker exited before recording a sandbox result",
)
```

Recovery fails both the step and run with durable reason metadata. It does not retry the
command because its prior side effects may be unknown.

Recover an uncertain running command from the CLI and print the resulting run with its
ordered steps. Reasons are `interrupted` or `timed_out`; operator detail is optional:

```bash
codex-agentic-os run recover step-001 timed_out
codex-agentic-os run recover step-001 interrupted \
  --detail "worker exited before recording a sandbox result"
```

Recovery requires an existing database and a running step. It never retries the
command.

Command arguments and timeouts are stored with the step and survive process restarts.
Steps may omit a command when they represent coordination-only work.

Record a sandbox result through the structural execution-result boundary. A zero exit
completes the step successfully and succeeds the run when every step is complete; a
nonzero exit fails both the step and run:

```python
step, run = runs.complete_step_from_result("step-001", result)
```

Inspect a durable run and its ordered steps without modifying runtime state:

```bash
codex-agentic-os run inspect run-002
codex-agentic-os run inspect run-002 --state-db /path/to/state.sqlite3
```

The default database is `.codex-agentic-os/state.sqlite3`. Inspection prints JSON and
fails without creating a database when the configured path does not exist.

List durable runs in stable run identifier order without loading their steps or
modifying runtime state:

```bash
codex-agentic-os run list
codex-agentic-os run list --state-db /path/to/state.sqlite3
```

Listing prints JSON summaries and fails without creating a missing database.

Cancel a queued or running run from the CLI. The command preserves completed steps,
cancels queued or running steps, and prints the resulting durable state as JSON:

```bash
codex-agentic-os run cancel run-002
codex-agentic-os run cancel run-002 --state-db /path/to/state.sqlite3
```

Cancellation requires an existing database and rejects terminal runs without changing
their state.

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
