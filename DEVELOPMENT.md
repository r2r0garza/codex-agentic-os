# Development

## Frontend stack and scaffolding

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

## Provider credentials

Provider integrations must remain independently usable: a missing API key for one provider must not block development or tests for other providers. Prefer injected transports and offline tests; require live credentials only for explicit integration or end-to-end verification.

When a task first requires environment-based configuration, add or update a committed `.env.example` containing placeholder values and ensure `.env` is ignored by Git. Do not commit secrets. The session performing that work must tell the user to copy `.env.example` to `.env`, identify which variables are required or optional, and clearly report any verification skipped because a key is unavailable.

List the default provider specs (kind, model, base URL, credential variable name, and
declared capabilities) without inspecting Python source. Credential output is limited
to environment-variable names; secret values are never read or printed, and the
command performs no network or state-database access:

```bash
codex-agentic-os provider list
```

Report whether each default provider's declared credential variable is non-empty.
Credential-free local providers report as configured. This is a local environment
readiness check only: values are never printed and credentials are not validated:

```bash
codex-agentic-os provider credentials
```

Send a single message through a configured provider adapter from the CLI. Omitted
`--model`/`--base-url`/`--api-key-env` fall back to the matching `DEFAULT_PROVIDER_SPECS`
entry for `--provider`, following the same endpoint/credential policy as the library
path (see [decision 0004](.decisions/0004-openai-compatible-endpoint-policy.md)):

```bash
codex-agentic-os chat send --provider anthropic --model claude-sonnet-4 "Summarize this repo"
codex-agentic-os chat send --provider lm_studio "hello"
```

Add an optional `--system TEXT` instruction ahead of the message. It maps to each
provider's native system-message shape (OpenAI-compatible `system` role message,
Anthropic top-level `system` field, Google `systemInstruction`); omitting it preserves
today's payloads exactly, and an empty or whitespace-only value is rejected before any
network call:

```bash
codex-agentic-os chat send --provider anthropic --model claude-sonnet-4 \
  --system "Answer in one sentence." "Summarize this repo"
```

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
codex-agentic-os agent register agent-1 --label "Build worker" \
  --state-db /path/to/state.sqlite3
codex-agentic-os agent heartbeat agent-1 --state-db /path/to/state.sqlite3
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

Perform the same explicit lifecycle transitions from the CLI. Terminal output must be
a JSON object and is accepted only for `succeeded` or `failed` transitions:

```bash
codex-agentic-os run transition run-001 running
codex-agentic-os run transition run-001 succeeded --output '{"artifacts": 4}'
```

Advance one durable step independently without executing its command. The confirmation
uses the same machine-readable shape as `run inspect-step`:

```bash
codex-agentic-os run transition-step step-001 running
codex-agentic-os run transition-step step-001 succeeded --output '{"artifacts": 4}'
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

List all durable runs in stable identifier order, repeat `--status` to include the
union of selected lifecycle states, or select one exact assigned agent:

```bash
codex-agentic-os run list
codex-agentic-os run list --status queued --status running
codex-agentic-os run list --agent-id agent-1
```

Let a worker atomically claim the next eligible queued, unassigned run without knowing
its identifier in advance. The command prints the standard ordered run payload when a
run is claimed, or `{"claim": {"attempted": false}}` when no eligible run exists:

```bash
codex-agentic-os run claim-next --agent-id agent-1
codex-agentic-os run claim-next --agent-id agent-1 --state-db /path/to/state.sqlite3
```

Creating an assigned run or claiming work requires an existing registered agent
identifier. No mutation occurs when the agent is unknown, no eligible run exists, or
validation otherwise fails.

Execute at most one queued step. Command steps require an explicitly selected
container backend; provider-message steps resolve their persisted provider and model
through the built-in provider configuration and do not require `--sandbox`:

```bash
codex-agentic-os run execute-next run-002 --sandbox docker
codex-agentic-os run execute-next run-002 --sandbox podman --image python:3.12-slim
codex-agentic-os run execute-next run-002 --sandbox docker \
  --mount /path/to/repository:/workspace --workdir /workspace
codex-agentic-os run execute-next run-002 --sandbox docker \
  --mount /path/to/repository:/workspace
codex-agentic-os run execute-next run-002 --sandbox docker \
  --env API_KEY=secret --env DEBUG=1
codex-agentic-os run execute-next run-002 --sandbox docker --network
codex-agentic-os run execute-next run-with-model-step
```

Container network access is disabled by default; `--network` is an explicit opt-in
that selects bridge networking for that one step. Omitting the flag preserves the
prior isolated (`--network none`) command construction exactly.

When no queued work remains, the unchanged run payload includes
`"execution": {"attempted": false}`. Successful provider responses are persisted as
normalized step output. Sandbox timeouts and interruptions leave the step running for
explicit recovery, since a subprocess result may be unknown. Adapter resolution and
transport failures (missing credentials, network errors, malformed responses) instead
durably fail the step and run with `{"error": ..., "error_type": ...}` output, since
those failures are definite and carry no uncertain side effect to reconcile.

Command arguments, timeouts, and provider-message inputs survive process restarts.
Every step requires exactly one command or provider message.

Append a queued command step to an existing durable run and print the updated ordered
run payload:

```bash
codex-agentic-os run add-step run-002 step-001 --objective "Run checks" \
  --timeout 30 --state-db .codex-agentic-os/state.sqlite3 -- pytest -q
```

Append a provider-message step with no trailing command. The provider and message are
required together; model, system, temperature, and token limit are optional:

```bash
codex-agentic-os run add-step run-002 step-002 --objective "Summarize output" \
  --provider ollama --message "Summarize the test output" --model llama3.1 \
  --system "Be concise" --temperature 0.2 --max-tokens 256 \
  --state-db .codex-agentic-os/state.sqlite3
```

Add `--approval-required` to either form to keep the step queued until an operator
records an explicit decision:

```bash
codex-agentic-os run add-step run-002 step-003 --objective "Publish result" \
  --approval-required --state-db .codex-agentic-os/state.sqlite3 -- publish-result
```

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
Failed steps include computed `failure_kind` and `retry_eligible` fields. Nonzero
command results and provider adapter errors are `definite` and retry-eligible;
recovered interrupted or timed-out outcomes are `uncertain` and ineligible because
their prior side effects may be unknown. Non-failed steps omit both fields.

Atomically requeue a `definite`, retry-eligible failed step as a new attempt through
the runtime's compare-and-swap path:

```python
new_step, run = runs.retry_step(
    "command", "command-retry",
    expected_step_revision=failed_step.revision,
    expected_run_revision=failed_run.revision,
)
```

`retry_step` rejects a non-`FAILED` step or an `uncertain` recovered outcome before
any mutation. On success it inserts exactly one new `QUEUED` step with the same
command/message/timeout/objective/approval requirement, returns the run to `queued`,
and appends one `step_retried` history entry linking the prior and new attempt
(`step_id` is the new attempt, `retried_step_id` is the retried one). The original
failed step's status, output, and history stay byte-for-byte unchanged; a stale
expected step or run revision is rejected without mutation, and concurrent retries
of the same failed step produce exactly one winner.

Perform the same operation explicitly from the CLI, supplying revisions from prior
inspection and a unique identifier for the new attempt:

```bash
codex-agentic-os run retry-step command command-retry \
  --expected-step-revision 3 --expected-run-revision 4
```

The standard run output shows `retried_into_step_id` on the failed attempt and
`retried_from_step_id` on the new attempt. A retried approval-required step returns to
`pending` and must use the existing `run approve` path before execution. Successful
completion ignores only prior failed attempts proven superseded by durable
`step_retried` history. There is no automatic or background retry, no backoff or retry
budget, and no compensation of external side effects.

List durable runs in stable run identifier order without loading their steps or
modifying runtime state:

```bash
codex-agentic-os run list
codex-agentic-os run list --state-db /path/to/state.sqlite3
```

Listing prints JSON summaries and fails without creating a missing database.

Inspect one run's durable lifecycle history in stable sequence order without
modifying runtime state. Each entry identifies the run, sequence, transition,
resulting status, responsible agent when known, execution kind (`command` or
`provider`), and step id when the entry is step-scoped; entries never include
credentials, raw environment values, command arguments, provider request bodies,
or terminal outputs:

```bash
codex-agentic-os run history run-002
codex-agentic-os run history run-002 --state-db /path/to/state.sqlite3
```

History inspection requires an existing database and run; it fails without
creating a database and without mutating state.

List one run's approval-required steps without exposing command arguments, provider
request bodies, credentials, raw environment values, or terminal output. The stable
JSON view includes approval and step status, execution kind, and requesting/deciding
agent identifiers when known:

```bash
codex-agentic-os run approvals run-002
codex-agentic-os run approvals run-002 --state-db /path/to/state.sqlite3
```

Approve or reject a pending request. An optional registered agent identifier records
who made the decision in durable history. Approval leaves the step queued and eligible
for normal execution; rejection fails the step and run without dispatching it:

```bash
codex-agentic-os run approve step-003 --agent-id operator-1
codex-agentic-os run reject step-003 --agent-id operator-1
```

Missing and already-decided steps are rejected without mutation.

Report whether a claimed run's owning agent is stale relative to an explicit
positive threshold, before any reassignment is attempted. Staleness compares
the owner's durable heartbeat (`agent heartbeat`/registration `last_seen`)
against the current time; a gap strictly greater than the threshold is
stale, a gap equal to or under it is fresh. The JSON view reports the run,
owner, last-seen time, threshold, evaluation time, and stale result:

```bash
codex-agentic-os run staleness run-002 --threshold-seconds 300
codex-agentic-os run staleness run-002 --threshold-seconds 300 --state-db /path/to/state.sqlite3
```

Evaluation is read-only. Unclaimed runs, unregistered owners, owners without a
recorded heartbeat, non-positive thresholds, and naive/ambiguous heartbeat
timestamps are rejected without mutation. There is no background monitoring or
automatic policy; `run staleness` only reports the inspection an operator uses
before deciding whether to reassign.

Transfer a demonstrably stale claim to a registered replacement agent. The
command requires the replacement agent id, the run's currently expected owner
and revision (as read from prior inspection), and an explicit positive
staleness threshold; it transfers ownership only through the runtime's atomic
compare-and-swap path and prints the resulting sanitized run:

```bash
codex-agentic-os run reassign-claim run-002 agent-b \
    --expected-agent-id agent-a --expected-revision 3 --threshold-seconds 300
codex-agentic-os run reassign-claim run-002 agent-b \
    --expected-agent-id agent-a --expected-revision 3 --threshold-seconds 300 \
    --state-db /path/to/state.sqlite3
```

A not-yet-stale owner, a stale expected owner/revision (lost contention), or
an unregistered replacement fail with no mutation. A running run's step
records are never touched; only run ownership and revision advance, and one
`claim_reassigned` history entry is appended and visible through `run
history`. There is no automatic or background reassignment, recovery or
retry of a running step, notification, or capacity policy — an operator must
invoke this command explicitly for each transfer.

Cancel a queued or running run from the CLI. The command preserves completed steps,
cancels queued or running steps, and prints the resulting durable state as JSON:

```bash
codex-agentic-os run cancel run-002
codex-agentic-os run cancel run-002 --state-db /path/to/state.sqlite3
```

Cancellation requires an existing database and rejects terminal runs without changing
their state.

Permanently remove a terminal run and all of its durable step history. The command
prints the removed run identifier and the number of removed steps; it never mutates
queued or running runs:

```bash
codex-agentic-os run prune run-002
codex-agentic-os run prune run-002 --state-db /path/to/state.sqlite3
```

Pruning requires an existing database, a run that exists, and a succeeded, failed, or
cancelled run status. There is no bulk cleanup, retention policy, or confirmation
prompt.

Register a durable agent identity and list registered agents in stable identifier
order. Registration rejects a duplicate agent id, and both commands reject
empty/whitespace-only values without mutation:

```bash
codex-agentic-os agent register agent-1 --label "Build worker"
codex-agentic-os agent inspect agent-1
codex-agentic-os agent list
codex-agentic-os agent list --state-db /path/to/state.sqlite3
```

The registry only records that an agent id exists; it does not track liveness and
`run claim`/`run add-step --agent-id` still accept any unchecked identifier.

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

## Pre-commit index refresh

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
