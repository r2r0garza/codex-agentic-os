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
  printf '%s\n' "Docker is required for the tool-call review" >&2
  exit 1
fi
rm -f "$STATE_DB" "$STATE_DB-shm" "$STATE_DB-wal"

codex-agentic-os run create "$RUN_ID" \
  --objective "Use one declared tool and preserve its durable evidence" \
  --state-db "$STATE_DB" >/dev/null
tool=$(jq -nc \
  '{name:"read_checkpoint",
    command:["/bin/sh","-c","printf \"review-tool-output\\n\""],
    description:"Read a deterministic checkpoint",
    parameters:{type:"object",properties:{}}}')
codex-agentic-os run add-step "$RUN_ID" "$STEP_ID" \
  --objective "Read and summarize the checkpoint" \
  --provider ollama --message "Use the declared checkpoint tool once" \
  --sandbox docker --image "$SANDBOX_IMAGE" --tool "$tool" \
  --state-db "$STATE_DB" >/dev/null

STATE_DB="$STATE_DB" RUN_ID="$RUN_ID" python3 - <<'PY'
import os

from codex_agentic_os.chat import ChatResponse, ChatToolCall
from codex_agentic_os.runtime import RunCoordinator
from codex_agentic_os.sandboxes import ContainerSandbox, SandboxSpec
from codex_agentic_os.state import StateStore


class ReviewAdapter:
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
                    arguments={"checkpoint": "current"},
                    call_id="review-call-1",
                ),
            )
        assert request.messages[-1].role == "tool"
        assert "review-tool-output" in request.messages[-1].content
        return ChatResponse("Checkpoint reconstructed.", model="review-model")


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


coordinator = RunCoordinator(StateStore(os.environ["STATE_DB"]))
step, run = coordinator.execute_next_step(
    os.environ["RUN_ID"],
    adapter_resolver=lambda _message: ReviewAdapter(),
    sandbox_resolver=sandbox_for,
)
assert step.status.value == "succeeded"
assert run.status.value == "succeeded"
PY

inspection=$(codex-agentic-os run inspect "$RUN_ID" --state-db "$STATE_DB")
history=$(codex-agentic-os run history "$RUN_ID" --state-db "$STATE_DB")

printf '%s' "$inspection" | jq -e \
  '.run.status == "succeeded"
   and .steps[0].status == "succeeded"
   and .steps[0].tool_declarations[0].name == "read_checkpoint"
   and .steps[0].tool_call.tool_name == "read_checkpoint"
   and .steps[0].tool_call.phase == "executed"
   and .steps[0].tool_call.exit_code == 0
   and .steps[0].tool_call.stdout == "review-tool-output\n"
   and .steps[0].output.content == "Checkpoint reconstructed."' >/dev/null
printf '%s' "$history" | jq -e \
  '([.[] | select(.tool_name == "read_checkpoint")
      | [.transition, .tool_outcome]])
   == [["tool_call_requested", "requested"],
       ["tool_call_executed", "succeeded"]]' >/dev/null
if printf '%s' "$history" | grep -q 'review-tool-output\|checkpoint.*current'; then
  printf '%s\n' "Tool arguments or terminal output leaked into durable history" >&2
  exit 1
fi

printf '%s\n' \
  "Tool-call history review passed: a provider step requested one declared tool," \
  "Docker executed it, the final response completed, and fresh CLI processes" \
  "reconstructed safe activity history plus the trusted durable step record."
