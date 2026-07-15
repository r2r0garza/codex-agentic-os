#!/bin/sh

set -eu

REPO_ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
STATE_DB=${STATE_DB:-/tmp/codex-agentic-os-tool-call-history-review.sqlite3}
RUN_ID=${RUN_ID:-tool-call-history-review}
STEP_ID=${STEP_ID:-use-review-tool}
SANDBOX_IMAGE=${SANDBOX_IMAGE:-python:3.12-slim}

for command in docker jq; do
  if ! command -v "$command" >/dev/null 2>&1; then
    printf '%s\n' "Required command is unavailable: $command" >&2
    exit 1
  fi
done
if [ ! -f "$REPO_ROOT/.venv/bin/activate" ]; then
  printf '%s\n' "Repository environment is unavailable: $REPO_ROOT/.venv" >&2
  exit 1
fi

# shellcheck disable=SC1091
. "$REPO_ROOT/.venv/bin/activate"
cd "$REPO_ROOT"
if ! docker info >/dev/null 2>&1; then
  printf '%s\n' "Docker is required for the tool-loop review" >&2
  exit 1
fi
rm -f "$STATE_DB" "$STATE_DB-shm" "$STATE_DB-wal"

codex-agentic-os run create "$RUN_ID" \
  --objective "Use a declared tool twice across worker replacement" \
  --state-db "$STATE_DB" >/dev/null
tool=$(jq -nc \
  '{name:"read_checkpoint",
    command:["/bin/sh","-c","printf \"review-tool-output\\n\""],
    description:"Read a deterministic checkpoint",
    parameters:{type:"object",properties:{}}}')
codex-agentic-os run add-step "$RUN_ID" "$STEP_ID" \
  --objective "Read two checkpoints and summarize them" \
  --provider ollama --message "Use the declared checkpoint tool twice" \
  --sandbox docker --image "$SANDBOX_IMAGE" --tool "$tool" \
  --tool-iteration-budget 2 \
  --state-db "$STATE_DB" >/dev/null

# Worker process 1 executes the first tool call durably, then simulates a crash
# while waiting for the next provider response.
STATE_DB="$STATE_DB" RUN_ID="$RUN_ID" STEP_ID="$STEP_ID" python3 - <<'PY'
import os

from codex_agentic_os.chat import ChatResponse, ChatToolCall
from codex_agentic_os.runtime import AgentRegistry, RunCoordinator
from codex_agentic_os.sandboxes import ContainerSandbox, SandboxSpec
from codex_agentic_os.state import StateStore
from codex_agentic_os.worker import run_worker


class CrashingAdapter:
    def __init__(self) -> None:
        self.calls = 0

    def complete(self, request):
        self.calls += 1
        if self.calls == 1:
            return ChatResponse(
                "",
                model="review-model",
                tool_call=ChatToolCall(
                    name="read_checkpoint",
                    arguments={"checkpoint": "first-private-value"},
                    call_id="review-call-1",
                ),
            )
        raise TimeoutError("simulated worker crash after the first durable execution")


def sandbox_for(policy):
    return ContainerSandbox(
        SandboxSpec(
            kind=policy.kind,
            image=policy.image,
            network_enabled=policy.network_enabled,
            mounts=policy.mounts,
            env=tuple((name, os.environ[name]) for name in policy.env_passthrough),
            working_dir=policy.working_dir,
        )
    )


store = StateStore(os.environ["STATE_DB"])
try:
    run_worker(
        RunCoordinator(store),
        AgentRegistry(store),
        "review-worker",
        heartbeat_interval=60,
        poll_interval=1,
        sandbox_resolver=sandbox_for,
        adapter_resolver=lambda _message: CrashingAdapter(),
        sleeper=lambda _seconds: None,
    )
except TimeoutError:
    pass
else:
    raise AssertionError("the first worker did not stop at the simulated crash")

step = RunCoordinator(StateStore(os.environ["STATE_DB"])).get_step(os.environ["STEP_ID"])
assert step is not None
assert step.status.value == "running"
assert len(step.tool_iterations) == 1
assert step.tool_iterations[0].tool_call.phase.value == "executed"
PY

# Worker process 2 has no in-memory state from process 1. It replays the first
# durable iteration, executes the second, and reaches the final response.
STATE_DB="$STATE_DB" RUN_ID="$RUN_ID" STEP_ID="$STEP_ID" python3 - <<'PY'
import os

from codex_agentic_os.chat import ChatResponse, ChatToolCall
from codex_agentic_os.runtime import AgentRegistry, RunCoordinator
from codex_agentic_os.sandboxes import ContainerSandbox, SandboxSpec
from codex_agentic_os.state import StateStore
from codex_agentic_os.worker import run_worker


class ReplacementAdapter:
    def __init__(self) -> None:
        self.calls = 0

    def complete(self, request):
        self.calls += 1
        assert request.messages[-1].role == "tool"
        if self.calls == 1:
            assert "review-tool-output" in request.messages[-1].content
            return ChatResponse(
                "",
                model="review-model",
                tool_call=ChatToolCall(
                    name="read_checkpoint",
                    arguments={"checkpoint": "second-private-value"},
                    call_id="review-call-2",
                ),
            )
        return ChatResponse("Both checkpoints reconstructed.", model="review-model")


def sandbox_for(policy):
    return ContainerSandbox(
        SandboxSpec(
            kind=policy.kind,
            image=policy.image,
            network_enabled=policy.network_enabled,
            mounts=policy.mounts,
            env=tuple((name, os.environ[name]) for name in policy.env_passthrough),
            working_dir=policy.working_dir,
        )
    )


remaining = [3]


def should_continue():
    remaining[0] -= 1
    return remaining[0] >= 0


store = StateStore(os.environ["STATE_DB"])
summary = run_worker(
    RunCoordinator(store),
    AgentRegistry(store),
    "review-worker",
    heartbeat_interval=60,
    poll_interval=1,
    sandbox_resolver=sandbox_for,
    adapter_resolver=lambda _message: ReplacementAdapter(),
    sleeper=lambda _seconds: None,
    should_continue=should_continue,
)
assert summary.executed_step_ids == (os.environ["STEP_ID"],)
coordinator = RunCoordinator(StateStore(os.environ["STATE_DB"]))
step = coordinator.get_step(os.environ["STEP_ID"])
run = coordinator.get(os.environ["RUN_ID"])
assert step is not None and step.status.value == "succeeded"
assert run is not None and run.status.value == "succeeded"
assert len(step.tool_iterations) == 2
PY

inspection=$(codex-agentic-os run inspect "$RUN_ID" --state-db "$STATE_DB")
history=$(codex-agentic-os run history "$RUN_ID" --state-db "$STATE_DB")

printf '%s' "$inspection" | jq -e \
  '.run.status == "succeeded"
   and .steps[0].status == "succeeded"
   and .steps[0].tool_declarations[0].name == "read_checkpoint"
   and ([.steps[0].tool_iterations[].tool_call.tool_name] == ["read_checkpoint", "read_checkpoint"])
   and ([.steps[0].tool_iterations[].tool_call.phase] == ["executed", "executed"])
   and ([.steps[0].tool_iterations[].tool_call.exit_code] == [0, 0])
   and ([.steps[0].tool_iterations[].tool_call.stdout] == ["review-tool-output\n", "review-tool-output\n"])
   and .steps[0].output.content == "Both checkpoints reconstructed."' >/dev/null
printf '%s' "$history" | jq -e \
  '([.[] | select(.tool_name == "read_checkpoint")
      | [.tool_iteration, .tool_phase, .tool_outcome]])
   == [[1, "requested", "requested"],
       [1, "executed", "succeeded"],
       [2, "requested", "requested"],
       [2, "executed", "succeeded"]]' >/dev/null
if printf '%s' "$history" | grep -q 'review-tool-output\|private-value'; then
  printf '%s\n' "Tool arguments or terminal output leaked into durable history" >&2
  exit 1
fi

printf '%s\n' \
  "Tool-loop history review passed: one worker durably executed iteration 1 and stopped," \
  "a replacement worker replayed it and completed iteration 2, and fresh CLI processes" \
  "reconstructed safe per-iteration history plus the trusted durable step record."
