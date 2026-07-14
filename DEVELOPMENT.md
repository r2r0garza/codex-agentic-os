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

Persist a complete sandbox policy on a command step so it later executes reproducibly
without re-supplying execution flags. `--env-passthrough` persists variable *names*
only; raw values are never written to durable state, and `--sandbox` is required
whenever any other policy flag is present:

```bash
codex-agentic-os run add-step run-002 step-004 --objective "Run in a fixed sandbox" \
  --sandbox docker --image python:3.12-slim --mount /path/to/repository:/workspace \
  --workdir /workspace --env-passthrough API_KEY --env-passthrough DEBUG --network \
  --state-db .codex-agentic-os/state.sqlite3 -- pytest -q
```

`run inspect` and `run inspect-step` show the persisted policy without a `sandbox_policy`
key when a command step has none. Dispatch a step with a persisted policy without
repeating sandbox flags:

```bash
API_KEY=secret DEBUG=1 codex-agentic-os run execute-next run-002 \
  --state-db .codex-agentic-os/state.sqlite3
```

Environment passthrough names are resolved from the executing process immediately
before sandbox construction. A missing name fails without starting the queued step,
and resolved values are redacted from the recorded sandbox command rather than
persisted. Per-invocation sandbox flags are rejected
when the next command step has a persisted policy; command steps without one retain
the legacy `run execute-next --sandbox ...` path.

Append a provider-message step with no trailing command. The provider and message are
required together; model, system, temperature, and token limit are optional:

```bash
codex-agentic-os run add-step run-002 step-002 --objective "Summarize output" \
  --provider ollama --message "Summarize the test output" --model llama3.1 \
  --system "Be concise" --temperature 0.2 --max-tokens 256 \
  --state-db .codex-agentic-os/state.sqlite3
```

Declare earlier steps from the same run as explicit provider context by repeating
`--context-step` in the order their persisted outputs should later be resolved. The
declaration stores and displays step ids only; it does not copy referenced outputs
into inspection payloads. Unknown steps and steps from another run are rejected
before the provider step is appended:

```bash
codex-agentic-os run add-step run-002 step-003 --objective "Compare results" \
  --provider ollama --message "Compare the prior results" \
  --context-step step-001 --context-step step-002 \
  --state-db .codex-agentic-os/state.sqlite3
```

Dispatch resolves every declared context reference against current step state
immediately before execution. A referencing step whose references have not all
succeeded stays queued and ineligible: `run execute-next` reports the gate
deterministically (exit code 2) without starting, failing, or otherwise
mutating the step, mirroring the approval gate's queued-but-ineligible
behavior. Once every reference has succeeded, dispatch proceeds and the
durable `step_started` history entry records the resolved reference ids
(no referenced output) as auditable evidence that resolution occurred.

Resolved references are sent to the adapter as explicit prior-context turns,
not folded into the current step's single message. For each declared
reference, in order, dispatch replays a `(user, assistant)` pair — the
referenced step's objective as the user turn and its durable output (a
provider step's response text, or a command step's rendered
`exit_code`/`stdout`/`stderr`) as the assistant turn — placed after any
system instruction and before the step's own current user message. Every
supported adapter family (OpenAI-compatible, Anthropic, Google) maps this
same ordered sequence into its native multi-message shape; a provider step
with no context references keeps today's exact single-turn payload.

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

Propose a durable plan draft for an existing run by dispatching its objective through a
configured provider adapter. The provider must respond with a single JSON object shaped
`{"steps": [<step>, ...]}`, where each `<step>` carries the same executable materialization
fields `add_step` requires so a later acceptance decision can pass it straight to the
existing queued-step creation path without guessing or synthesizing execution details. A
command step is `{"objective": "...", "execution_kind": "command", "command": [...],
"sandbox_policy": {"kind": "docker" or "podman", ...}, "timeout": <optional>}` and must not
include `"message"`; a provider step is `{"objective": "...", "execution_kind": "provider",
"message": {"provider": "...", "content": "...", ...}}` and must not include `"command"`,
`"timeout"`, or `"sandbox_policy"`. Each proposed step's `step_id` is materialized
deterministically from the plan id and its 1-based position (e.g. `draft-1-step-1`), never
taken from the model, so ids are collision-free within one draft by construction and
inspectable ahead of any future acceptance decision. No steps are queued by this command,
and `--objective` overrides the text sent for planning without changing the run's own
stored objective:

```bash
codex-agentic-os run plan run-002 draft-1 --provider ollama --model llama3.1 \
  --state-db .codex-agentic-os/state.sqlite3
codex-agentic-os run plan run-002 draft-2 --provider ollama \
  --objective "Re-plan after the schema change" \
  --state-db .codex-agentic-os/state.sqlite3
```

A well-formed proposal is durably persisted as a `draft` plan awaiting an explicit
operator acceptance decision. A malformed or unparseable proposal is instead persisted
as an `invalid` plan carrying the raw provider response as evidence, and the command
fails explicitly (exit code 2) naming the recorded plan id; the run's step queue is
unchanged either way.

Inspect a durable plan draft without modifying runtime state, showing every proposed
step's objective, execution kind, materialized step id, and executable payload (command
plus sandbox policy, or provider message) in stable order:

```bash
codex-agentic-os run inspect-plan draft-1 --state-db .codex-agentic-os/state.sqlite3
```

The same JSON shape `run plan` prints on success is reused here, so a `draft` status is
reviewable and an `invalid` status carries its recorded error and raw evidence. A plan id
with no durable record fails explicitly (exit code 2) without creating a database, draft,
run, or step.

Accept or reject the inspected draft by supplying its current revision. Acceptance
atomically queues every proposed step in stable order and marks the plan `accepted`;
rejection marks it `rejected` and queues nothing. Both commands compare-and-swap the
reviewed draft and attached run snapshots, so a stale or competing decision fails
without partially materializing steps or overwriting the winning decision. Acceptance
also rejects a materialized step id that already exists. An optional registered agent id
is recorded with the plan-identified run-history decision:

```bash
codex-agentic-os run accept-plan draft-1 --expected-revision 1 \
  --agent-id operator-1 --state-db .codex-agentic-os/state.sqlite3
codex-agentic-os run reject-plan draft-2 --expected-revision 1 \
  --agent-id operator-1 --state-db .codex-agentic-os/state.sqlite3
```

The command prints the standard plan JSON with its terminal status, incremented
revision, and `decision_agent_id` when one was supplied. Accepted steps enter the same
queued approval, eligibility, execution, retry, and history paths as manually added
steps; there is no planner-specific execution path.

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
`provider`), and step id when the entry is step-scoped; a `step_started` entry
for a context-referencing provider step additionally carries the resolved
`context_step_ids` in declared order as evidence that resolution occurred.
Entries never include credentials, raw environment values, command arguments,
provider request bodies, or terminal outputs:

```bash
codex-agentic-os run history run-002
codex-agentic-os run history run-002 --state-db /path/to/state.sqlite3
```

History inspection requires an existing database and run; it fails without
creating a database and without mutating state.

Show one run's provider usage evidence in durable step order plus a
run-level token aggregate. Each provider step reports its status, provider,
resolved model, and a `usage` block (`available`, `input_tokens`,
`output_tokens`, `raw`, `unavailable_reason`); command steps are omitted, and
steps that have not yet completed or whose adapter reported no usage are
shown with usage explicitly unavailable rather than fabricated zero counts.
The aggregate reports how many steps have available versus unavailable usage
and sums tokens only over the available ones:

```bash
codex-agentic-os run usage run-002
codex-agentic-os run usage run-002 --state-db /path/to/state.sqlite3
```

Usage inspection requires an existing database and run; it fails without
creating a database and without mutating state, and never includes
credentials, raw environment values, request bodies, or prompt content.

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

Run a foreground autonomous worker for one durable agent identity. The command
registers a new agent id, or heartbeats and resumes an already-registered one, then
repeatedly claims and executes queued run steps until stopped:

```bash
codex-agentic-os worker run --agent-id agent-1 \
  --heartbeat-interval 30 --poll-interval 5
codex-agentic-os worker run --agent-id agent-1 --label "Build worker" \
  --heartbeat-interval 30 --poll-interval 5 --state-db /path/to/state.sqlite3
```

Both intervals must be positive and are validated before any registration or
heartbeat mutation. Each iteration prefers a `queued` run already assigned to the
worker's agent id (from a prior `run create --agent-id` or an earlier worker
session) over claiming a new unassigned one, then executes that run's queued steps
in order through the same durable coordinator `run execute-next` uses, stopping
once the run reaches a terminal status. Command steps dispatch only from their
persisted sandbox policy — as with `run execute-next`, a command step without one
fails through the existing explicit error path rather than receiving worker-supplied
flags. Provider-message steps resolve an adapter from the step's declared provider
and model, identically to `run execute-next`. Completed step and run outcomes are
visible from a separate process via `run inspect` and `run history`.

When the next queued step on a claimed run is approval-gated or has an unresolved
`context_step_ids` reference, the worker leaves that step and run untouched — it
does not call the execution backend — and moves on to another assigned or
claimable run instead of raising or retrying the same blocked step in a tight
loop. If no other eligible work exists, the worker idles for exactly one
`poll-interval` sleep before re-checking, so a step that was blocked only because
an earlier step hadn't finished, or an operator decision was still pending, is
retried automatically on a later poll cycle once state changes out of band (for
example, `run approve`).

`worker run` installs SIGINT and SIGTERM handlers around its loop instead of
letting either signal raise: receiving either sets an internal flag that the
worker's own `should_continue` check consults at its existing between-step
boundary, so the process exits with the normal JSON summary and status 0 —
never a raw traceback — and never corrupts the state database. A step already
executing when the signal arrives still runs to completion and is recorded
durably before the worker stops; the worker never force-completes or
re-dispatches a step. If a step is ever left `running` because its owning
worker process was killed outright (for example `SIGKILL`, which cannot be
caught), a later `worker run` or `run execute-next` on that run raises rather
than silently completing or duplicating the step, and an operator reconciles
it explicitly with `run recover` or transfers ownership with `run
reassign-claim`, exactly as for any other uncertain running step.

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
