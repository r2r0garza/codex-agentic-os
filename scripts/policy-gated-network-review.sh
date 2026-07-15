#!/bin/sh

set -eu

REPO_ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
STATE_DB=${STATE_DB:-/tmp/codex-agentic-os-policy-gated-network-review.sqlite3}
SANDBOX_IMAGE=${SANDBOX_IMAGE:-python:3.12-slim}
RUN_ID=${RUN_ID:-policy-gated-network-review}
STEP_ID=${STEP_ID:-network-enabled-step}
AGENT_ID=${AGENT_ID:-policy-gated-network-worker}
RULE_ID=${RULE_ID:-network-review}
RULE_REASON=${RULE_REASON:-Network access needs operator review}

WORKER_PID=
WORKER_LOG=/tmp/codex-agentic-os-policy-gated-network-worker.log

cleanup() {
  trap - EXIT INT TERM
  if [ -n "$WORKER_PID" ] && kill -0 "$WORKER_PID" 2>/dev/null; then
    kill -TERM "$WORKER_PID" 2>/dev/null || true
    wait "$WORKER_PID" 2>/dev/null || true
  fi
}
trap cleanup EXIT INT TERM

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
  printf '%s\n' "Docker is required for the real worker-executed network step" >&2
  exit 1
fi

rm -f "$STATE_DB" "$STATE_DB-shm" "$STATE_DB-wal"

# This review needs no live provider credentials: the gated step is a plain
# command step, and network access only enables the container's network
# namespace, not any outbound provider call.
codex-agentic-os agent register "$AGENT_ID" \
  --label "Policy-gated network worker" --state-db "$STATE_DB" >/dev/null
codex-agentic-os run create "$RUN_ID" \
  --objective "Execute a network-enabled step under policy review" \
  --agent-id "$AGENT_ID" --state-db "$STATE_DB" >/dev/null
codex-agentic-os run add-step "$RUN_ID" "$STEP_ID" \
  --objective "Run a command with container network access enabled" \
  --sandbox docker --image "$SANDBOX_IMAGE" --network --state-db "$STATE_DB" \
  -- /bin/sh -c 'printf "policy-gated-network-step-completed\n"' >/dev/null
codex-agentic-os policy create "$RULE_ID" \
  --criterion-kind sandbox_network_access --criterion-value enabled \
  --reason "$RULE_REASON" --precedence 10 --state-db "$STATE_DB" >/dev/null

codex-agentic-os worker run --agent-id "$AGENT_ID" \
  --heartbeat-interval 1 --poll-interval 0.2 --state-db "$STATE_DB" \
  > "$WORKER_LOG" 2>&1 &
WORKER_PID=$!

held=false
attempt=0
while [ "$attempt" -lt 120 ]; do
  step=$(codex-agentic-os run inspect-step "$STEP_ID" --state-db "$STATE_DB")
  approvals=$(codex-agentic-os run approvals "$RUN_ID" --state-db "$STATE_DB")
  if printf '%s' "$step" | jq -e \
      '.status == "queued"
       and .policy_rule_id == "'"$RULE_ID"'"
       and .policy_reason == "'"$RULE_REASON"'"' >/dev/null \
    && printf '%s' "$approvals" | jq -e \
      '.[0].approval_status == "pending"' >/dev/null; then
    held=true
    break
  fi
  if ! kill -0 "$WORKER_PID" 2>/dev/null; then
    printf '%s\n' "Worker stopped before the policy gate held the step:" >&2
    sed -n '1,160p' "$WORKER_LOG" >&2
    exit 1
  fi
  attempt=$((attempt + 1))
  sleep 0.25
done

if [ "$held" != true ]; then
  printf '%s\n' "Timed out waiting for the policy gate to hold the network step" >&2
  exit 1
fi

codex-agentic-os run approve "$STEP_ID" --agent-id "$AGENT_ID" --state-db "$STATE_DB" >/dev/null

completed=false
attempt=0
while [ "$attempt" -lt 120 ]; do
  inspect=$(codex-agentic-os run inspect "$RUN_ID" --state-db "$STATE_DB")
  if printf '%s' "$inspect" | jq -e \
      '.run.status == "succeeded"
       and .steps[0].status == "succeeded"
       and .steps[0].output.exit_code == 0' >/dev/null; then
    completed=true
    break
  fi
  if ! kill -0 "$WORKER_PID" 2>/dev/null; then
    printf '%s\n' "Worker stopped before the approved step completed:" >&2
    sed -n '1,160p' "$WORKER_LOG" >&2
    exit 1
  fi
  attempt=$((attempt + 1))
  sleep 0.25
done

if [ "$completed" != true ]; then
  printf '%s\n' "Timed out waiting for the approved network step to complete" >&2
  exit 1
fi

kill -TERM "$WORKER_PID"
wait "$WORKER_PID" 2>/dev/null || true
WORKER_PID=

# Every command below runs in a fresh process against only the durable
# sqlite state, so this reconstructs the policy decision exactly as an
# operator would after a restart -- no in-memory state survives from the
# worker or approval commands above.
step_after_restart=$(codex-agentic-os run inspect-step "$STEP_ID" --state-db "$STATE_DB")
history=$(codex-agentic-os run history "$RUN_ID" --state-db "$STATE_DB")

printf '%s' "$step_after_restart" | jq -e \
  '.status == "succeeded"
   and .policy_rule_id == "'"$RULE_ID"'"
   and .policy_reason == "'"$RULE_REASON"'"' >/dev/null

printf '%s' "$history" | jq -e \
  '([.[] | select(.transition == "step_policy_gated" and .step_id == "'"$STEP_ID"'")]
     | length == 1 and .[0].policy_rule_id == "'"$RULE_ID"'"
       and .[0].policy_reason == "'"$RULE_REASON"'")
   and ([.[].transition] | index("step_approved") != null)
   and ([.[] | select(.transition == "step_started" and .step_id == "'"$STEP_ID"'")] | length == 1)
   and ([.[] | select(.transition == "step_succeeded" and .step_id == "'"$STEP_ID"'")] | length == 1)
   and (.[-1].transition == "run_succeeded")' >/dev/null

printf '%s\n' \
  "Policy-gated network review passed:" \
  "- step $STEP_ID was automatically held by policy rule $RULE_ID before dispatch" \
  "- the held step was approved and executed exactly once through the normal worker/dispatch path" \
  "- durable history reconstructs the policy decision (rule id and reason) after a process restart" \
  "- the run reached run_succeeded with no live provider credentials involved"
