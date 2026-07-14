#!/bin/sh

set -eu

REPO_ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
STATE_DB=${STATE_DB:-/tmp/codex-agentic-os-delegation-interruption-review.sqlite3}
PARENT_AGENT=${PARENT_AGENT:-delegation-parent-agent}
CHILD_AGENT=${CHILD_AGENT:-delegation-child-agent}
PARENT_RUN=${PARENT_RUN:-delegation-parent-run}
PARENT_STEP=${PARENT_STEP:-delegate-review}
CHILD_RUN=${CHILD_RUN:-delegate-review-child}
CHILD_STEP=${CHILD_STEP:-perform-review}
SANDBOX_IMAGE=${SANDBOX_IMAGE:-python:3.12-slim}
PARENT_LOG=${PARENT_LOG:-/tmp/codex-agentic-os-delegation-parent-worker.log}
CHILD_LOG=${CHILD_LOG:-/tmp/codex-agentic-os-delegation-child-worker.log}

PARENT_PID=
CHILD_PID=

cleanup() {
  trap - EXIT INT TERM
  for pid in "$PARENT_PID" "$CHILD_PID"; do
    if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
      kill -TERM "$pid" 2>/dev/null || true
    fi
  done
  for pid in "$PARENT_PID" "$CHILD_PID"; do
    if [ -n "$pid" ]; then
      wait "$pid" 2>/dev/null || true
    fi
  done
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
  printf '%s\n' "Docker is required for the delegated command step" >&2
  exit 1
fi
rm -f "$STATE_DB" "$STATE_DB-shm" "$STATE_DB-wal" "$PARENT_LOG" "$CHILD_LOG"

codex-agentic-os agent register "$PARENT_AGENT" \
  --label "Delegating parent worker" --state-db "$STATE_DB" >/dev/null
codex-agentic-os agent register "$CHILD_AGENT" \
  --label "Delegated child worker" --state-db "$STATE_DB" >/dev/null
codex-agentic-os run create "$PARENT_RUN" \
  --objective "Delegate and incorporate an independent review" \
  --agent-id "$PARENT_AGENT" --state-db "$STATE_DB" >/dev/null
codex-agentic-os run add-step "$PARENT_RUN" "$PARENT_STEP" \
  --objective "Delegate the review" \
  --delegate-objective "Review the parent result after worker interruption" \
  --delegate-target-agent "$CHILD_AGENT" --state-db "$STATE_DB" >/dev/null

codex-agentic-os worker run --agent-id "$PARENT_AGENT" \
  --heartbeat-interval 0.2 --poll-interval 0.05 --state-db "$STATE_DB" \
  >"$PARENT_LOG" 2>&1 &
PARENT_PID=$!

attempt=0
while [ "$attempt" -lt 200 ]; do
  parent=$(codex-agentic-os run inspect "$PARENT_RUN" --state-db "$STATE_DB")
  if printf '%s' "$parent" | jq -e \
      '.run.status == "running"
       and .steps[0].status == "running"
       and .steps[0].delegated_run_id == "'"$CHILD_RUN"'"' >/dev/null; then
    break
  fi
  if ! kill -0 "$PARENT_PID" 2>/dev/null; then
    printf '%s\n' "Parent worker stopped before dispatching delegation" >&2
    sed -n '1,160p' "$PARENT_LOG" >&2
    exit 1
  fi
  attempt=$((attempt + 1))
  sleep 0.05
done
if [ "$attempt" -eq 200 ]; then
  printf '%s\n' "Timed out waiting for delegation dispatch" >&2
  exit 1
fi

# Interrupt the parent worker while its durable delegation step is parked on
# the queued child. The child continues through its ordinary assigned lifecycle.
kill -TERM "$PARENT_PID"
wait "$PARENT_PID"
PARENT_PID=

codex-agentic-os run add-step "$CHILD_RUN" "$CHILD_STEP" \
  --objective "Produce the independent review" --sandbox docker \
  --image "$SANDBOX_IMAGE" \
  --state-db "$STATE_DB" -- /bin/sh -c 'printf "delegated-review-passed\n"' >/dev/null
codex-agentic-os worker run --agent-id "$CHILD_AGENT" \
  --heartbeat-interval 0.2 --poll-interval 0.05 --state-db "$STATE_DB" \
  >"$CHILD_LOG" 2>&1 &
CHILD_PID=$!

attempt=0
while [ "$attempt" -lt 200 ]; do
  child=$(codex-agentic-os run inspect "$CHILD_RUN" --state-db "$STATE_DB")
  if printf '%s' "$child" | jq -e \
      '.run.status == "succeeded"
       and .run.parent_run_id == "'"$PARENT_RUN"'"
       and .run.parent_step_id == "'"$PARENT_STEP"'"
       and .steps[0].status == "succeeded"' >/dev/null; then
    break
  fi
  if ! kill -0 "$CHILD_PID" 2>/dev/null; then
    printf '%s\n' "Child worker stopped before completing delegated work" >&2
    sed -n '1,160p' "$CHILD_LOG" >&2
    exit 1
  fi
  attempt=$((attempt + 1))
  sleep 0.05
done
if [ "$attempt" -eq 200 ]; then
  printf '%s\n' "Timed out waiting for child completion" >&2
  exit 1
fi
kill -TERM "$CHILD_PID"
wait "$CHILD_PID"
CHILD_PID=

# A fresh parent-worker process resumes the same identity and reconciles the
# parked step solely from the child's durable terminal outcome.
codex-agentic-os worker run --agent-id "$PARENT_AGENT" \
  --heartbeat-interval 0.2 --poll-interval 0.05 --state-db "$STATE_DB" \
  >>"$PARENT_LOG" 2>&1 &
PARENT_PID=$!

attempt=0
while [ "$attempt" -lt 200 ]; do
  parent=$(codex-agentic-os run inspect "$PARENT_RUN" --state-db "$STATE_DB")
  if printf '%s' "$parent" | jq -e \
      '.run.status == "succeeded"
       and .steps[0].status == "succeeded"
       and .steps[0].output.child_run_id == "'"$CHILD_RUN"'"
       and .steps[0].output.child_agent_id == "'"$CHILD_AGENT"'"
       and .steps[0].output.child_status == "succeeded"' >/dev/null; then
    break
  fi
  if ! kill -0 "$PARENT_PID" 2>/dev/null; then
    printf '%s\n' "Restarted parent worker stopped before reconciliation" >&2
    sed -n '1,200p' "$PARENT_LOG" >&2
    exit 1
  fi
  attempt=$((attempt + 1))
  sleep 0.05
done
if [ "$attempt" -eq 200 ]; then
  printf '%s\n' "Timed out waiting for parent reconciliation" >&2
  exit 1
fi

parent_history=$(codex-agentic-os run history "$PARENT_RUN" --state-db "$STATE_DB")
child_history=$(codex-agentic-os run history "$CHILD_RUN" --state-db "$STATE_DB")
printf '%s' "$parent_history" | jq -e \
  '([.[] | select(.transition == "step_delegated"
                  and .delegated_run_id == "'"$CHILD_RUN"'")] | length == 1)
   and (.[-1].transition == "run_succeeded")' >/dev/null
printf '%s' "$child_history" | jq -e \
  '.[0].transition == "created"
   and .[0].parent_run_id == "'"$PARENT_RUN"'"
   and .[0].parent_step_id == "'"$PARENT_STEP"'"' >/dev/null

kill -TERM "$PARENT_PID"
wait "$PARENT_PID"
PARENT_PID=

printf '%s\n' \
  "Delegation interruption review passed: two registered agents executed the" \
  "linked parent/child workflow, the parent worker was interrupted and restarted," \
  "and durable inspection/history reconstructed the terminal child outcome."
