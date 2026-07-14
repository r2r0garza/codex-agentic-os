#!/bin/sh

set -eu

REPO_ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
STATE_DB=${STATE_DB:-/tmp/codex-agentic-os-dashboard-review.sqlite3}
API_PORT=${API_PORT:-8080}
DASHBOARD_PORT=${DASHBOARD_PORT:-3000}
SANDBOX_IMAGE=${SANDBOX_IMAGE:-python:3.12-slim}
RUN_ID=${RUN_ID:-dashboard-review}
AGENT_ID=${AGENT_ID:-dashboard-review-worker}

API_PID=
DASHBOARD_PID=
WORKER_PID=
STATE_HASH=

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

  if [ -n "$STATE_HASH" ] && [ -f "$STATE_DB" ]; then
    final_hash=$(openssl dgst -sha256 "$STATE_DB" | awk '{print $2}')
    if [ "$final_hash" = "$STATE_HASH" ]; then
      printf '%s\n' "Read-only check passed: durable database hash stayed $STATE_HASH"
    else
      printf '%s\n' "Read-only check failed: durable database changed while serving the review" >&2
      exit 1
    fi
  fi
}
trap cleanup EXIT INT TERM

for command in curl docker jq openssl pnpm; do
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
  --label "Dashboard review worker" --state-db "$STATE_DB" >/dev/null
codex-agentic-os run create "$RUN_ID" \
  --objective "Review worker progress from the browser" \
  --agent-id "$AGENT_ID" --state-db "$STATE_DB" >/dev/null
codex-agentic-os run add-step "$RUN_ID" command-step \
  --objective "Execute work in the sandbox" \
  --sandbox docker --image "$SANDBOX_IMAGE" --state-db "$STATE_DB" \
  -- /bin/sh -c 'printf "worker-completed\n"' >/dev/null
codex-agentic-os run add-step "$RUN_ID" approval-step \
  --objective "Publish the reviewed result" \
  --provider ollama --message "Publish the reviewed result" \
  --approval-required --state-db "$STATE_DB" >/dev/null

codex-agentic-os worker run --agent-id "$AGENT_ID" \
  --heartbeat-interval 1 --poll-interval 0.2 --state-db "$STATE_DB" \
  > /tmp/codex-agentic-os-dashboard-worker.log 2>&1 &
WORKER_PID=$!

ready=false
attempt=0
while [ "$attempt" -lt 120 ]; do
  inspect=$(codex-agentic-os run inspect "$RUN_ID" --state-db "$STATE_DB")
  approvals=$(codex-agentic-os run approvals "$RUN_ID" --state-db "$STATE_DB")
  if printf '%s' "$inspect" | jq -e \
      '.steps[0].status == "succeeded" and .steps[1].status == "queued"' >/dev/null \
    && printf '%s' "$approvals" | jq -e \
      '.[0].approval_status == "pending"' >/dev/null; then
    ready=true
    break
  fi
  if ! kill -0 "$WORKER_PID" 2>/dev/null; then
    printf '%s\n' "Worker stopped before producing the review state:" >&2
    sed -n '1,160p' /tmp/codex-agentic-os-dashboard-worker.log >&2
    exit 1
  fi
  attempt=$((attempt + 1))
  sleep 0.25
done

if [ "$ready" != true ]; then
  printf '%s\n' "Timed out waiting for the worker-executed mixed run" >&2
  exit 1
fi

kill -TERM "$WORKER_PID"
wait "$WORKER_PID"
WORKER_PID=

STATE_HASH=$(openssl dgst -sha256 "$STATE_DB" | awk '{print $2}')

codex-agentic-os api serve --host 127.0.0.1 --port "$API_PORT" \
  --state-db "$STATE_DB" > /tmp/codex-agentic-os-dashboard-api.log 2>&1 &
API_PID=$!

cd "$REPO_ROOT/dashboard"
API_BASE_URL="http://127.0.0.1:$API_PORT" \
  pnpm dev --hostname 127.0.0.1 --port "$DASHBOARD_PORT" \
  > /tmp/codex-agentic-os-dashboard-ui.log 2>&1 &
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

printf '%s\n' \
  "Dashboard review is ready at http://127.0.0.1:$DASHBOARD_PORT" \
  "Select run $RUN_ID and confirm:" \
  "  - command-step is first and succeeded" \
  "  - approval-step is second and queued" \
  "  - Publish the reviewed result is visibly pending" \
  "  - provider usage is explicitly unavailable, not fabricated as zero" \
  "  - the only interactive control is read-only navigation" \
  "Press Ctrl-C to stop both servers and verify the database hash."

while kill -0 "$API_PID" 2>/dev/null && kill -0 "$DASHBOARD_PID" 2>/dev/null; do
  sleep 1
done

printf '%s\n' "API or dashboard exited unexpectedly" >&2
exit 1
