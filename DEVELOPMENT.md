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

Inspect the ordered provider preference policy used for capability-routed
steps. The default follows `DEFAULT_PROVIDER_SPECS` order; repeat
`--provider-preference` to inspect an explicit dispatch override:

```bash
codex-agentic-os provider routing-policy
codex-agentic-os provider routing-policy \
  --provider-preference anthropic --provider-preference openai
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

Declare named workspace artifacts on a command step with `--artifact NAME=PATH`. Each
path must be an absolute path that resolves within one of the step's persisted sandbox
mounts (`--mount HOST:CONTAINER`); declaring an artifact without a persisted mount, or
with a path outside every mount, fails before mutation:

```bash
codex-agentic-os run add-step run-002 step-005 --objective "Build the release archive" \
  --sandbox docker --mount /path/to/repository:/workspace --workdir /workspace \
  --artifact archive=/workspace/dist/release.tar.gz \
  --state-db .codex-agentic-os/state.sqlite3 -- python build.py
```

After a successful command execution (`exit_code` zero), each declared file is read
from its resolved host-side mount path and captured into local durable artifact
storage keyed by content hash and byte size, alongside the producing run and step
identity. A declared file absent after execution is recorded as an explicit `absent`
artifact without turning a succeeded command step into a failed one, and a declared
file over the configured size limit is recorded `rejected` with its actual size rather
than being read into memory. `run inspect` and `run inspect-step` show each step's
`artifact_declarations` and, once execution completes, an `artifacts` list with each
record's name, status, content hash, and size — never the resolved host path, command
arguments, or environment values.

Append a provider-message step with no trailing command. The message is required
together with exactly one of `--provider` (a fixed dispatch target) or `--capability`
(a required capability resolved to a provider at dispatch time); declaring both, or
declaring a capability no default provider spec declares, fails before the step is
written. Model, system, temperature, and token limit are optional:

```bash
codex-agentic-os run add-step run-002 step-002 --objective "Summarize output" \
  --provider ollama --message "Summarize the test output" --model llama3.1 \
  --system "Be concise" --temperature 0.2 --max-tokens 256 \
  --response-artifact summary \
  --state-db .codex-agentic-os/state.sqlite3
```

`--response-artifact NAME` declares that the successful normalized response text
should also be captured as a durable artifact. The response's UTF-8 bytes use the
same local storage, hash, byte-size, producing run/step identity, size-limit, and
history contracts as command artifacts; inspection identifies the source as
`response.content`. Oversize content is recorded as `rejected` without changing the
provider step's successful output, while adapter or routing failures capture no
artifact. Existing normalized output, provider usage evidence, routing provenance,
and context behavior are unchanged, and durable artifact history never includes the
request body, raw provider envelope, credentials, or environment values.

`run list-artifacts RUN_ID [--step STEP_ID]` lists one run's command-step and
provider-response artifact records — captured, absent, and rejected — in stable
`(step_id, name)` order, each with its producing run id, step id, name, status,
content hash, and size, but never the resolved host path, command arguments,
environment values, or terminal output. `run export-artifact RUN_ID --name NAME
[--step STEP_ID] --destination PATH` writes one captured artifact's stored content
byte-identical to an operator-chosen path; `--step` disambiguates a name declared by
more than one step. Exporting an absent, rejected, missing, or ambiguous artifact
fails cleanly without writing the destination or mutating durable state:

```bash
codex-agentic-os run list-artifacts run-002 --state-db .codex-agentic-os/state.sqlite3
codex-agentic-os run export-artifact run-002 --name archive --step step-005 \
  --destination /path/to/release.tar.gz --state-db .codex-agentic-os/state.sqlite3
```

Declare a named tool on a provider-message step with `--tool JSON`, repeatable for
multiple tools. Each tool is `{"name": "...", "command": ["<argv0>", ...],
"description": "<optional>", "parameters": {<optional JSON schema object>}}`; `name`
must be a valid identifier, unique among the step's tools, and `command` a non-empty
list of non-empty strings. A tool-declaring provider step also requires a persisted
`--sandbox` policy — the same durable policy a command step uses — since a declared
tool's command later executes through that same sandbox boundary. Declaring a
sandbox policy on a provider step that declares no tools remains rejected exactly as
before:

A tool-declaring provider step also requires an explicit
`--tool-iteration-budget COUNT` — a positive integer maximum number of
tool-execution iterations the step may run before it must complete with a
final response. There is no default and no implicit unlimited loop: a
tool-declaring step created without a budget, or with a zero, negative, or
non-integer budget, is rejected before any run or step mutation. Steps that
declare no tools reject `--tool-iteration-budget` exactly like they reject
`--sandbox`.

```bash
codex-agentic-os run add-step run-002 step-007 --objective "Summarize with a tool" \
  --provider ollama --message "Summarize the repository" \
  --sandbox docker --mount /path/to/repository:/workspace \
  --tool '{"name": "list_files", "command": ["ls", "-la"], "description": "List workspace files"}' \
  --tool-iteration-budget 3 \
  --state-db .codex-agentic-os/state.sqlite3
```

`run inspect` and `run inspect-step` show each step's `tool_declarations` (name,
command, optional description, optional parameters), its `tool_iteration_budget`,
and, when present, its `sandbox_policy`; a provider step declaring no tools omits
all three keys exactly as before. A durable step persisted before this budget
existed and declares tools without one still loads for trusted inspection with
`tool_iteration_budget` omitted, rather than failing closed; only step creation
requires the budget explicitly. At dispatch, the provider-neutral request carries only the tool name,
optional description, and object-input schema — never the sandbox command template.
OpenAI-compatible adapters emit `tools` with function definitions, Anthropic emits
native tools with `input_schema`, and Google emits `functionDeclarations`. Omitting
tools leaves every prior adapter payload unchanged. A missing parameters schema maps
deterministically to an empty object schema; a schema whose root is explicitly
non-object is rejected before transport.

When a model's response requests a declared tool by name, `execute_next_step`
durably appends an ordered iteration containing the complete normalized model
response and request (name, arguments, provider call id) before any
sandboxed command runs, executes that tool's declared command template
unmodified — the model's requested arguments are recorded as evidence only and
are never interpolated into the executed command — through the step's own
persisted sandbox policy, durably persists the command result, then issues the
next request carrying every prior assistant/tool turn. The bounded loop repeats
until the model returns a final response. A budget of `N` permits at most `N`
sandbox tool executions; if the model requests another tool after those
executions, that response is persisted as `rejected_budget`, no command runs,
and the step/run fail definitively with the complete ordered evidence. Both
phases are written while the step stays `running`, so an interruption between
them leaves an inspectable `tool_call` (`phase` `requested` or `executed`).
A step whose last durable iteration is still `requested` is a genuinely
uncertain in-progress execution and remains for `run recover`, which always
fails it definitively without resuming or re-executing the tool, exactly like
recovery for a command or provider step. A step whose last durable iteration
is `executed` is a safe boundary instead: calling `execute_next_step` again —
whether from `run execute-next` or a replacement `worker run` process reusing
the same durable state — replays every completed iteration's assistant/tool
turns from stored evidence and issues the next provider request, without
re-executing any already-durable sandboxed command. A run or step cancelled
while a tool loop is in progress is checked at the top of every loop pass
before it acts on a provider response and, on a conflicting concurrent write,
when persisting a requested or executed phase; either path stops the loop
with no further provider request or sandbox execution, leaving completed
iterations exactly as durably recorded and no more added. A model request for
a tool name the step never declared or a tool call with no sandbox resolver
available fails the step definitively without executing a command. Provider
responses containing multiple simultaneous calls remain rejected by every
adapter before dispatch.
`run inspect`/`run inspect-step` show `tool_iterations` in order, including
each normalized provider response and call outcome, plus `tool_call` as a
compatibility alias for the latest call; a step with no tool call omits both
keys exactly as before. `worker run` and `run execute-next` both pass the same
persisted sandbox resolver used for command steps; `run execute-next` only
supplies it when the next step declares tools.

`run history` identifies each durable tool-loop mutation with a one-based
`tool_iteration`, its persisted `tool_phase`, the non-sensitive tool name, and
a bounded outcome: `requested`, `succeeded`, `failed`, `rejected_undeclared`,
or `rejected_budget`. It never duplicates model arguments, command argv,
environment values, provider request bodies, stdout, or stderr. An undeclared
request's rejection entry is atomic with the definitive step/run failure and
remains eligible for the established failed-step retry classification. Trusted
local CLI inspection still shows the full durable step record; the loopback
HTTP run-detail surface redacts tool declaration commands, every iteration's
provider content/raw response, and tool-call arguments, commands, stdout, and
stderr under Decision 0008.

With Docker and `jq` available, run the committed worker-replacement proof:

```bash
./scripts/tool-call-history-review.sh
```

The first worker process stops after one executed iteration is durable. A
second process reconstructs that iteration from SQLite, performs the second
tool call, reaches the final provider response, and verifies both trusted
inspection and the safe per-iteration history projection.

```bash
codex-agentic-os run add-step run-002 step-002b --objective "Summarize output" \
  --capability general --message "Summarize the test output" \
  --state-db .codex-agentic-os/state.sqlite3
```

At dispatch, a capability step selects the first configured provider in the
explicit preference order that declares the capability. The selected
provider's default model is used unless the step declared `--model`.
`run execute-next` and `worker run` accept repeated `--provider-preference`
flags; omitting them uses the inspectable default policy. Fixed-provider
steps bypass this routing policy. Selection is deterministic for identical
provider specs, policy order, and message input.

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

Declare a child-run delegation step with `--delegate-objective`, an alternative execution
input mutually exclusive with a command or provider message. `--delegate-target-agent`
optionally pre-assigns a registered agent to the spawned child run; supplying it without
`--delegate-objective` fails before mutation:

```bash
codex-agentic-os run add-step run-002 step-006 --objective "Delegate the review" \
  --delegate-objective "Review the proposed change" --delegate-target-agent agent-2 \
  --state-db .codex-agentic-os/state.sqlite3
```

`run execute-next` dispatches a queued delegation step atomically: it creates one new
child run (id `{step_id}-child`) durably linked to the parent run and step, transitions
the parent run to `running` if it was still queued, and leaves the parent step `running`
with the child run id recorded as `delegated_run_id`. The child run appears in `run
list`/`run inspect` like any other run, with `parent_run_id` and `parent_step_id`
identifying its parent; it is claimable and executable through the entirely unchanged
existing lifecycle, including approvals, artifacts, and history. A competing or repeated
dispatch of the same step cannot spawn a second child: the parent step's own
compare-and-swap revision check inside the atomic dispatch transaction rejects it.

When both sides have assigned agents, declaration rejects delegation to the current
run's own agent (self-delegation) or back to any assigned ancestor run's agent (a
detectable delegation cycle) before appending the step. Unassigned runs and unassigned
delegations remain valid because they do not declare an agent edge that can be checked.
`run inspect` shows `delegated_run_id` on the parent step and `parent_run_id` plus
`parent_step_id` on the child run. The corresponding `step_delegated` and child
`created` history entries persist the same identifiers, so either history can be read
without correlating raw database rows.

Calling `run execute-next` again reconciles the delegation. While the child is queued or
running it reports `{"execution": {"attempted": false}}`; the underlying coordinator
raises `DelegationPendingError`, which the worker loop treats like an approval or
context-reference gate and moves on to other work. Once the child is terminal, the same
call atomically records `child_run_id`, `child_status`, optional `child_agent_id`, and the
child's terminal output on the parent step. A succeeded child succeeds the delegation
step and either advances the parent to its next queued step or succeeds the completed
parent run. A failed or cancelled child fails the delegation step and parent run
explicitly. These parent history entries use `execution_kind: "delegation"`, and stale
step/run revisions reject reconciliation without overwriting a competing transition.
Reconciliation also rejects a child run whose recorded `parent_run_id`/`parent_step_id`
no longer matches the delegating step, rather than reconciling against an unrelated run.

Cancelling a run with an active delegation step cascades to its linked child: an
active (`queued` or `running`) child run, and any of its own active steps, are
cancelled atomically alongside the parent, so a cancelled parent never leaves a
delegated child running unattended with no parent step left to reconcile its
outcome. A child that already reached a terminal status is left untouched. This
cascade is the one automatic child-cancellation policy the runtime applies; it
also rejects cancellation outright if a child's linkage no longer matches its
parent, rather than silently cancelling an unrelated run.

`run recover`/`recover_running_step` exists to fail a running command or provider
step whose subprocess or adapter call may have crashed without a durable result — it
does not apply to delegation steps, whose `running` status is a legitimate parked
state while a child executes, not an uncertain in-process execution. Calling it on a
delegation step is rejected explicitly; recover the linked child run's own step
instead, or let `run execute-next` reconcile the parent once the child reaches a
terminal status.

Run the reproducible two-agent interruption review from the repository root:

```bash
scripts/delegation-interruption-review.sh
```

It uses an isolated `/tmp` database, registers distinct parent and child agents,
interrupts the parent worker after atomic child dispatch, lets the child worker execute
a Docker-sandboxed `printf` step, then starts a fresh parent-worker process and asserts
the parent completes from the child's durable terminal outcome. Final assertions cover
both inspection directions and both linkage-bearing history entries.

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
"message": {"content": "...", ..., "provider": "..."} or {"content": "...", ...,
"required_capability": "..."}}` (exactly one of `"provider"` or `"required_capability"`)
and must not include `"command"`, `"timeout"`, or `"sandbox_policy"`. Each proposed step's `step_id` is materialized
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

The full operator review — objective, `run plan`, `run inspect-plan`, `run accept-plan`
or `run reject-plan`, `run execute-next` through the unchanged worker/coordinator path,
and reconstruction from `run inspect-plan`/`run inspect`/`run history` after a simulated
process restart — is exercised end to end by
`tests/test_run_cli.py::test_cli_end_to_end_operator_review_reconstructs_plan_execution_after_restart`,
with `test_cli_rejected_plan_remains_reconstructable_with_no_executable_steps_after_restart`
covering the rejection path. No draft step is queued or executable before its plan is
explicitly accepted; running the two tests directly reproduces the reviewed flow without
a live provider or container backend.

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
For a capability-routed provider step, the same `step_started` entry carries
`required_capability`, `resolved_provider`, `resolved_model`, and a stable
`routing_reason`. These fields persist across restart and contain no
credentials or request body. `required_capability` reflects the step's
requested capability even when routing fails to resolve a provider; when no
configured provider (under the effective routing policy) declares the
capability at dispatch time, the step fails definitively with an explicit
`"no configured provider satisfies required capability: ..."` reason,
`resolved_provider`/`resolved_model`/`routing_reason` stay `null`, and the
parent run fails alongside it exactly as any other definite step failure.
Fixed-provider steps are unaffected and continue to bypass routing entirely.
Entries never include credentials, raw environment values, command arguments,
provider request bodies, or terminal outputs:

```bash
codex-agentic-os run history run-002
codex-agentic-os run history run-002 --state-db /path/to/state.sqlite3
```

History inspection requires an existing database and run; it fails without
creating a database and without mutating state.

`run watch RUN_ID --interval SECONDS` polls the same durable history read
model on the given positive interval and prints one JSON object per line —
each new history entry exactly once in sequence order, reusing the `run
history` redaction contract — until the run reaches a terminal status or the
operator interrupts the command (Ctrl-C or SIGTERM), at which point the
command exits cleanly without error. A watch session tracks its own
in-process sequence cursor, so no entry already printed this session repeats.
Each history line includes its durable `sequence`; pass the last observed value
back as `--after-sequence N` when restarting a watcher to emit only entries
with a greater sequence. The cursor must be a non-negative integer and remains
operator-provided: the watcher does not persist client state.
When the next queued step is blocked on a pending approval, the session
prints one `{"event": "blocked", ...}` notice identifying the blocking step;
it is not repeated on later polls unless a different step becomes blocked.
Watching requires an existing database and run and a positive `--interval`,
validated before the database is opened; it never creates a database or
mutates run, step, history, approval, artifact, usage, or agent state:

```bash
codex-agentic-os run watch run-002 --interval 2
codex-agentic-os run watch run-002 --interval 2 --after-sequence 41
codex-agentic-os run watch run-002 --interval 2 --state-db /path/to/state.sqlite3
```

`codex-agentic-os api serve --port PORT` starts a local HTTP server that
reuses the exact `run list`/`run inspect`/`run history`/`run
approvals`/`run usage` JSON contracts over loopback HTTP, so operator
interfaces beyond the CLI can be built on stable contracts. `--host` must be
an explicit loopback literal such as `127.0.0.1` (the default) or `::1`; a
hostname like `localhost` or any non-loopback address is rejected before a
socket is ever opened. Read routes always use the read-only state connection
the server was started with. Routes are served under a stable `/api/v1`
base path:

- `GET /api/v1/runs` — the same payload as `run list`.
- `GET /api/v1/runs/{run_id}` — the same payload as `run inspect`,
  including ordered steps, except that a step's declared command argv,
  declared provider `message.content`/`system`, and a completed step's
  captured terminal output (`stdout`/`stderr`) and provider response
  (`content`/`raw`) are each replaced with `"<redacted>"`. Non-sensitive
  metadata (provider, model, status, the sanitized sandbox invocation
  command) is shown exactly as `run inspect` shows it. See Decision 0008
  for why the HTTP surface redacts declared input and captured output that
  the CLI does not.
- `GET /api/v1/runs/{run_id}/history` — the same payload as `run history`.
- `GET /api/v1/runs/{run_id}/approvals` — the same sanitized payload as
  `run approvals`.
- `GET /api/v1/runs/{run_id}/usage` — the same provider-step evidence and
  aggregate as `run usage`, including explicitly unavailable usage.

Four `POST` mutation routes delegate to the same durable,
compare-and-swap-safe `RunCoordinator` operations the CLI's `run
approve`/`run reject`/`run cancel`/`run retry-step` commands use, opening a
writable state connection only for the duration of that one request:

- `POST /api/v1/runs/{run_id}/steps/{step_id}/approve` — approve a step
  pending operator approval.
- `POST /api/v1/runs/{run_id}/steps/{step_id}/reject` — reject a step
  pending operator approval; fails the step and its run.
- `POST /api/v1/runs/{run_id}/steps/{step_id}/retry` — requeue an eligible
  failed step as a new attempt. The JSON body must supply integer
  `expected_step_revision` and `expected_run_revision` fields (read from a
  prior `GET` of the run), matching the CLI's required
  `--expected-step-revision`/`--expected-run-revision`; the retried step's
  id is generated by the server.
- `POST /api/v1/runs/{run_id}/cancel` — cancel an active run and its
  queued/running steps.

Every mutation route returns the same redacted run-detail payload as `GET
/api/v1/runs/{run_id}` on success (`200`), reflecting the durable outcome
rather than an optimistic guess. An unknown run or step returns `404`; a
malformed or missing request body returns `400`; an ineligible step/run
state or a genuine compare-and-swap conflict (for example a stale retry
raced against another decision) returns `409` — no partial mutation happens
in either failure case. Competing CLI and HTTP decisions cannot double-execute
a step or overwrite a terminal decision, because `approve`/`reject`/`cancel`
re-read current state immediately before their compare-and-swap write, and
`retry` requires the caller's own last-observed revisions.

An unrecognized path or unknown run id returns a structured `{"error":
...}` JSON body with `404`; any unsupported method on a route — including
`GET`'s own routes under `POST`, the four mutation routes under any method
but `POST`, and any method with no built-in handler such as `OPTIONS`,
`TRACE`, or `CONNECT` — returns the same shape with `405` and an `Allow:
GET` header. Every other server-detected failure (an unparseable request
line, an unsupported HTTP version) also returns this `{"error": ...}` JSON
shape rather than the stdlib's default HTML error page. The server runs in
the foreground until interrupted (Ctrl-C or SIGTERM), exiting cleanly like
`run watch` and `worker run`:

```bash
codex-agentic-os api serve --port 8080
codex-agentic-os api serve --host ::1 --port 8080 --state-db /path/to/state.sqlite3
```

For a local operator acceptance review, create or copy a state database to a
temporary path, then start the server explicitly on loopback:

```bash
codex-agentic-os api serve --host 127.0.0.1 --port 8080 --state-db /tmp/codex-agentic-os-uat.sqlite3
```

From another terminal, inspect the mixed run through all five contracts:

```bash
curl http://127.0.0.1:8080/api/v1/runs
curl http://127.0.0.1:8080/api/v1/runs/RUN_ID
curl http://127.0.0.1:8080/api/v1/runs/RUN_ID/history
curl http://127.0.0.1:8080/api/v1/runs/RUN_ID/approvals
curl http://127.0.0.1:8080/api/v1/runs/RUN_ID/usage
```

The responses are JSON, steps remain in durable order, pending approvals and
available or unavailable usage are explicit, and declared command
argv/provider request content plus captured terminal/provider output are
each `"<redacted>"`. Requests use only `127.0.0.1`; comparing `run
inspect` and `run history` before and after the review should show no durable
change. Press Ctrl-C in the server terminal and confirm it exits without a
traceback. The consolidated offline regression in `tests/test_api.py` performs
the same five-endpoint, redaction, loopback-only, and no-mutation review against
a temporary mixed-run database.

### Running the dashboard against the API

`dashboard/` is a Next.js operator dashboard over the same loopback API. Run
detail offers contextual, confirmed mutation controls (approve/reject a
pending approval, cancel an active run, retry a retry-eligible failed step);
see "Dashboard mutation controls" below. For the reproducible Sprint 18
operator review, install the committed frontend dependencies once, ensure
Docker is running, then launch the review harness from the repository root:

```bash
cd dashboard
pnpm install
cd ..
./scripts/dashboard-operator-review.sh
```

The harness activates the repository `.venv`, creates a fresh isolated state
database at `/tmp/codex-agentic-os-dashboard-review.sqlite3`, and registers a
dedicated worker. That real worker executes the first command step through a
Docker sandbox and stops after the second, provider-backed step reaches its
approval gate. The harness then starts the API on `127.0.0.1:8080` and the
dashboard on `127.0.0.1:3000` against that durable state. The harness itself
never approves, rejects, cancels, retries, or otherwise mutates the run after
serving begins — it only observes what the browser shows.

Open `http://127.0.0.1:3000`, select `dashboard-review`, and confirm all of the
following browser-visible evidence:

- The run is `running`, with `command-step` first and `succeeded` after real
  worker execution, followed by queued `approval-step`.
- Lifecycle history includes the run and command-step start/success transitions
  with `dashboard-review-worker` provenance.
- “Publish the reviewed result” appears under Pending approvals with a visible
  `pending` badge and Approve/Reject controls; a Cancel run control appears
  next to the running status badge; no Retry control appears anywhere, since
  no step has failed.
- Provider usage lists the provider step in durable order and displays
  `unavailable` token evidence rather than fabricated zero values.
- Clicking Approve, Reject, or Cancel run opens an explicit confirmation
  dialog before sending anything; closing it via "Back" leaves the run
  untouched. Do not confirm any of them during this read-only review — #119
  covers a scripted end-to-end demonstration of confirming one.

Press Ctrl-C in the harness terminal when the review is complete. It stops both
servers and compares the state database's SHA-256 digest with the digest taken
immediately before serving; `Read-only check passed` confirms the browser/API
review did not change durable state. The database is recreated on the next run.
Set `STATE_DB`, `API_PORT`, `DASHBOARD_PORT`, `SANDBOX_IMAGE`, `RUN_ID`, or
`AGENT_ID` before the command only when a local conflict requires an override.

For the mutable Sprint 19 approval review, run the separate harness instead:

```bash
./scripts/dashboard-approval-review.sh
```

It recreates only its isolated state database at
`/tmp/codex-agentic-os-dashboard-approval-review.sqlite3`, registers a
dedicated worker, and queues one Docker command step behind an approval gate.
The worker first executes `preflight-command-step` through Docker so the run
reaches `running` through real dispatch, then remains active while the second
command waits at its approval gate. The API and dashboard listen explicitly on
`127.0.0.1`; the harness checks the API's actual listening socket before
allowing the review to continue.

Open the printed dashboard URL, select `dashboard-approval-review`, click
Approve, verify the “Approve this step?” dialog appears, and click “Confirm
approve.” The worker must then execute `approved-command-step` through Docker.
The harness checks durable state for an approved request, a succeeded step and
run, exactly one `step_started` and `step_succeeded` transition for the
approval-gated step, and final `run_succeeded`. It leaves the servers open
after printing `Approval review
passed` so the dashboard can poll and show the persisted succeeded state plus
`step_approved`, `step_started`, `step_succeeded`, and `run_succeeded` history.
Press Ctrl-C after inspecting that evidence. The database is recreated on the
next run, and no repository or non-isolated state is mutated.

Set `STATE_DB`, `API_PORT`, `DASHBOARD_PORT`, `SANDBOX_IMAGE`, `RUN_ID`,
`STEP_ID`, or `AGENT_ID` only to avoid a local conflict. Unlike the Sprint 18
review above, this review intentionally mutates its isolated database through
the same dashboard proxy and loopback mutation API used in production; it does
not replace the read-only hash check.

For an existing database instead, start both processes manually. In one
terminal, serve the API exactly as above:

```bash
codex-agentic-os api serve --host 127.0.0.1 --port 8080 --state-db /tmp/codex-agentic-os-uat.sqlite3
```

In a second terminal, point the dashboard at that loopback base URL and start
its dev server. `dashboard/.env.example` documents `API_BASE_URL` (defaulting
to `http://127.0.0.1:8080`), read by the dashboard's server-side proxy routes:

```bash
cd dashboard
cp .env.example .env
pnpm install
pnpm dev
```

Open `http://localhost:3000`. The browser only ever calls the dashboard's own
same-origin `/api/v1/runs` and `/api/v1/runs/{run_id}[/history|/approvals|/usage]`
`GET` routes, plus `/api/v1/runs/{run_id}/cancel` and
`/api/v1/runs/{run_id}/steps/{step_id}/{approve,reject,retry}` `POST` mutation
routes (`app/api/v1/runs/route.ts`, `app/api/v1/runs/[...segments]/route.ts`);
those routes forward each request to `API_BASE_URL` server-side, where the
browser's CORS policy does not apply. Confirm the run list, ordered steps,
lifecycle history, pending approvals, and provider usage render from the
API's JSON exactly, that declared command argv and provider
`message.content`/`system`/`stdout`/`stderr`/`content`/`raw` never appear as
plaintext in the rendered page (the dashboard has no code path that
reconstructs a redacted field from other local state), and that approve/reject
appear only for a pending approval, cancel only for an active run, and retry
only for a retry-eligible failed step — each behind an explicit confirmation
dialog, with no bare form or unconfirmed action anywhere in the UI. `pnpm
test` in `dashboard/` includes a regression that renders a run whose fields
carry sentinel values in every field the Sprint 17 API contract redacts and
asserts none of them appear in the rendered output, plus coverage for
contextual mutation-control visibility, confirmation gating, and durable-state
refresh after a mutation succeeds or fails.

#### Dashboard mutation controls

Run detail (`components/run-detail.tsx`) renders each control only when the
durable state it last polled makes the action eligible: approve/reject next to
a pending approval, cancel next to an active (`queued`/`running`) run's status
badge, and retry in a failed step's row only when the API's `retry_eligible`
field is `true`. Every control requires an explicit confirmation dialog before
it calls `lib/api.ts`'s `approveStep`/`rejectStep`/`cancelRun`/`retryStep`,
which `POST` through the dashboard's own same-origin proxy
(`app/api/v1/runs/[...segments]/route.ts`) to the four mutation routes #117
added. After every attempt — success or failure — the run detail bundle is
reloaded from the API immediately (`usePollingLoad`'s `refresh()`) rather than
guessing the outcome locally, so a stale or competing decision (another
dashboard session or the CLI already acted) surfaces the API's structured
error message and the actually-current durable state, with no control shown
for an action that is no longer eligible after the refresh.

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
their state. Cancelling a run with an active delegation step also cancels its linked
child run (and any of the child's own active steps) atomically; see the delegation
section above for the cascade and linkage-mismatch details.

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

Persist and inspect finite-criterion execution policy rules. `create` rejects an
unknown criterion kind, a malformed value, an empty reason, a negative precedence,
and a duplicate rule id without mutation; `list` and `inspect` are read-only and
never contact a provider, sandbox, or step:

```bash
codex-agentic-os policy create rule-1 \
  --criterion-kind sandbox_network_access --criterion-value disabled \
  --reason "Deny network access by default" --precedence 10
codex-agentic-os policy create rule-2 \
  --criterion-kind declared_tool_name --criterion-value search_files \
  --reason "Only search_files may run without approval" --precedence 0 --disabled
codex-agentic-os policy list
codex-agentic-os policy inspect rule-1 --state-db /path/to/state.sqlite3
```

`--criterion-kind` is restricted to the finite enumerated set
`sandbox_network_access`, `declared_tool_name`, and `execution_kind`; each kind
further restricts `--criterion-value` to its own closed value set or identifier
shape (`enabled`/`disabled`; a valid identifier; `command`/`provider`/`delegation`).
There is no way to persist a compound or free-form expression — see Decision 0009.
`--disabled` persists the rule with `enabled: false`; rules are enabled by default.
Before `run execute-next` or a worker can dispatch a queued step, enabled rules are
matched against that step's durable attributes. The lowest `(precedence, rule_id)`
match marks the step approval-required through the existing approval flow and adds a
`step_policy_gated` history entry containing the rule id and reason. Approving or
rejecting the step uses the ordinary `run approve`/`run reject` commands; disabled
and non-matching rules do not change dispatch, and rule creation never rewrites an
approval that has already been decided.

With Docker and `jq` available, run the committed policy-gated network review:

```bash
./scripts/policy-gated-network-review.sh
```

It creates a network-enabled command step, a matching `sandbox_network_access`
rule, and a real `worker run` process; waits for the policy gate to hold the
step; approves and executes it through the normal worker/dispatch path; then,
in fresh CLI processes against only the durable database, reconstructs the
triggering rule id and reason from `run history` and confirms the run reached
`run_succeeded`. The step is a plain command, so the review needs no live
provider credentials.

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
