#!/bin/sh

set -eu

REPO_ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
STATE_DB=${STATE_DB:-/tmp/codex-agentic-os-dashboard-approval-review.sqlite3}
API_PORT=${API_PORT:-8080}
DASHBOARD_PORT=${DASHBOARD_PORT:-3000}
SANDBOX_IMAGE=${SANDBOX_IMAGE:-python:3.12-slim}
RUN_ID=${RUN_ID:-dashboard-approval-review}
STEP_ID=${STEP_ID:-approved-command-step}
AGENT_ID=${AGENT_ID:-dashboard-approval-worker}

API_PID=
DASHBOARD_PID=
WORKER_PID=

cleanup() {
  trap - EXIT INT TERM
  for pid in "$DASHBOARD_PID" "$API_PID" "$WORKER_PID"; do
    if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
      kill -TERM "$pid" 2>/dev/null || true
    fi
  done
  for pid in "$DASHBOARD_PID" "$API_PID" "$WORKER_PID"; do
    if [ -n "$pid" ]; then
      wait "$pid" 2>/dev/null || true
    fi
  done
}
trap cleanup EXIT INT TERM

for command in curl docker jq lsof pnpm; do
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
  printf '%s\n' "Docker is required for the real worker-executed command step" >&2
  exit 1
fi

rm -f "$STATE_DB" "$STATE_DB-shm" "$STATE_DB-wal"

codex-agentic-os agent register "$AGENT_ID" \
  --label "Dashboard approval worker" --state-db "$STATE_DB" >/dev/null
codex-agentic-os run create "$RUN_ID" \
  --objective "Approve and complete work from the browser" \
  --agent-id "$AGENT_ID" --state-db "$STATE_DB" >/dev/null
codex-agentic-os run add-step "$RUN_ID" preflight-command-step \
  --objective "Reach the approval gate through real worker dispatch" \
  --sandbox docker --image "$SANDBOX_IMAGE" --state-db "$STATE_DB" \
  -- /bin/sh -c 'printf "approval-review-ready\n"' >/dev/null
codex-agentic-os run add-step "$RUN_ID" "$STEP_ID" \
  --objective "Execute the approved work exactly once" \
  --approval-required --sandbox docker --image "$SANDBOX_IMAGE" \
  --state-db "$STATE_DB" \
  -- /bin/sh -c 'printf "browser-approved-worker-completed\n"' >/dev/null

codex-agentic-os worker run --agent-id "$AGENT_ID" \
  --heartbeat-interval 1 --poll-interval 0.2 --state-db "$STATE_DB" \
  > /tmp/codex-agentic-os-dashboard-approval-worker.log 2>&1 &
WORKER_PID=$!

ready=false
attempt=0
while [ "$attempt" -lt 120 ]; do
  inspect=$(codex-agentic-os run inspect "$RUN_ID" --state-db "$STATE_DB")
  approvals=$(codex-agentic-os run approvals "$RUN_ID" --state-db "$STATE_DB")
  if printf '%s' "$inspect" | jq -e \
      '.run.status == "running"
       and .steps[0].status == "succeeded"
       and .steps[1].status == "queued"' >/dev/null \
    && printf '%s' "$approvals" | jq -e \
      '.[0].approval_status == "pending"' >/dev/null; then
    ready=true
    break
  fi
  if ! kill -0 "$WORKER_PID" 2>/dev/null; then
    printf '%s\n' "Worker stopped before reaching the approval gate:" >&2
    sed -n '1,160p' /tmp/codex-agentic-os-dashboard-approval-worker.log >&2
    exit 1
  fi
  attempt=$((attempt + 1))
  sleep 0.25
done

if [ "$ready" != true ]; then
  printf '%s\n' "Timed out waiting for the worker to reach the approval gate" >&2
  exit 1
fi

codex-agentic-os api serve --host 127.0.0.1 --port "$API_PORT" \
  --state-db "$STATE_DB" > /tmp/codex-agentic-os-dashboard-approval-api.log 2>&1 &
API_PID=$!

cd "$REPO_ROOT/dashboard"
API_BASE_URL="http://127.0.0.1:$API_PORT" \
  pnpm dev --hostname 127.0.0.1 --port "$DASHBOARD_PORT" \
  > /tmp/codex-agentic-os-dashboard-approval-ui.log 2>&1 &
DASHBOARD_PID=$!

attempt=0
while [ "$attempt" -lt 120 ]; do
  if curl --fail --silent "http://127.0.0.1:$API_PORT/api/v1/runs" >/dev/null \
    && curl --fail --silent "http://127.0.0.1:$DASHBOARD_PORT" >/dev/null; then
    break
  fi
  if ! kill -0 "$API_PID" 2>/dev/null || ! kill -0 "$DASHBOARD_PID" 2>/dev/null; then
    printf '%s\n' "API or dashboard stopped during startup" >&2
    exit 1
  fi
  attempt=$((attempt + 1))
  sleep 0.25
done

if [ "$attempt" -eq 120 ]; then
  printf '%s\n' "Timed out waiting for the API and dashboard" >&2
  exit 1
fi

if ! lsof -nP -a -p "$API_PID" -iTCP -sTCP:LISTEN -Fn \
    | grep -Fx "n127.0.0.1:$API_PORT" >/dev/null; then
  printf '%s\n' "API is not listening exclusively on expected loopback address 127.0.0.1:$API_PORT" >&2
  lsof -nP -a -p "$API_PID" -iTCP -sTCP:LISTEN >&2 || true
  exit 1
fi

printf '%s\n' \
  "Dashboard approval review is ready at http://127.0.0.1:$DASHBOARD_PORT" \
  "API loopback check passed: 127.0.0.1:$API_PORT" \
  "Select run $RUN_ID, click Approve, then confirm with Confirm approve." \
  "The real worker will execute $STEP_ID in Docker after the durable approval."

completed=false
while kill -0 "$API_PID" 2>/dev/null \
  && kill -0 "$DASHBOARD_PID" 2>/dev/null \
  && kill -0 "$WORKER_PID" 2>/dev/null; do
  inspect=$(codex-agentic-os run inspect "$RUN_ID" --state-db "$STATE_DB")
  if printf '%s' "$inspect" | jq -e \
      '.run.status == "succeeded"
       and .steps[0].status == "succeeded"
       and .steps[1].status == "succeeded"
       and .steps[1].output.exit_code == 0' >/dev/null; then
    completed=true
    break
  fi
  sleep 0.25
done

if [ "$completed" != true ]; then
  printf '%s\n' "A review process stopped before the approved run completed" >&2
  sed -n '1,160p' /tmp/codex-agentic-os-dashboard-approval-worker.log >&2
  exit 1
fi

approvals=$(codex-agentic-os run approvals "$RUN_ID" --state-db "$STATE_DB")
history=$(codex-agentic-os run history "$RUN_ID" --state-db "$STATE_DB")
printf '%s' "$approvals" | jq -e \
  '.[0].approval_status == "approved"' >/dev/null
printf '%s' "$history" | jq -e \
  '([.[].transition] | index("step_approved") != null)
   and ([.[] | select(.transition == "step_started" and .step_id == "'"$STEP_ID"'")] | length == 1)
   and ([.[] | select(.transition == "step_succeeded" and .step_id == "'"$STEP_ID"'")] | length == 1)
   and (.[-1].transition == "run_succeeded")' >/dev/null

kill -TERM "$WORKER_PID"
wait "$WORKER_PID"
WORKER_PID=

printf '%s\n' \
  "Approval review passed: durable history contains step_approved, exactly one" \
  "start/success for $STEP_ID, and terminal run_succeeded." \
  "Confirm the dashboard shows the succeeded run and lifecycle history, then press Ctrl-C."

while kill -0 "$API_PID" 2>/dev/null && kill -0 "$DASHBOARD_PID" 2>/dev/null; do
  sleep 1
done

printf '%s\n' "API or dashboard exited unexpectedly" >&2
exit 1
