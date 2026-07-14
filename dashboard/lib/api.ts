/**
 * Typed client for the dashboard's own same-origin `/api/v1/*` proxy
 * routes, which forward to the codex-agentic-os read-only loopback HTTP
 * API (`codex-agentic-os api serve`). The dashboard and that API run on
 * different ports, so the browser's CORS policy blocks a direct
 * cross-origin fetch; the proxy route (`app/api/v1/runs/route.ts`) does
 * the cross-origin call server-side, where CORS does not apply. Every
 * shape here mirrors the JSON contracts documented in DEVELOPMENT.md and
 * built by `payloads.py` — this file must not invent divergent fields.
 */

export type RunStatus =
  "queued" | "running" | "succeeded" | "failed" | "cancelled"

export interface RunSummary {
  run_id: string
  objective: string
  status: RunStatus
  revision: number
  agent_id: string | null
  output: Record<string, unknown> | null
}

export type StepStatus = RunStatus

export interface RunStep {
  step_id: string
  run_id: string
  position: number
  objective: string
  status: StepStatus
  revision: number
  command?: string | string[] | null
  message?: {
    provider: string | null
    model: string | null
  }
  output: Record<string, unknown> | null
}

export interface RunDetail {
  run: RunSummary
  steps: RunStep[]
}

export interface RunHistoryEntry {
  run_id: string
  sequence: number
  transition: string
  status: string
  agent_id: string | null
  execution_kind: string | null
  step_id: string | null
}

export type ApprovalStatus = "pending" | "approved" | "rejected"

export interface ApprovalRequest {
  step_id: string
  run_id: string
  position: number
  objective: string
  step_status: StepStatus
  approval_required: true
  approval_status: ApprovalStatus | null
  execution_kind: "command" | "provider"
  requesting_agent_id: string | null
  deciding_agent_id: string | null
}

export interface StepUsage {
  step_id: string
  position: number
  status: StepStatus
  provider: string
  model: string | null
  usage: {
    available: boolean
    input_tokens: number | null
    output_tokens: number | null
    raw: unknown
    unavailable_reason: string | null
  }
}

export interface RunUsage {
  run_id: string
  steps: StepUsage[]
  aggregate: {
    steps_with_usage_available: number
    steps_with_usage_unavailable: number
    input_tokens: number | null
    output_tokens: number | null
  }
}

export interface RunDetailBundle {
  detail: RunDetail
  history: RunHistoryEntry[]
  approvals: ApprovalRequest[]
  usage: RunUsage
}

export class ApiError extends Error {}

async function parseErrorBody(response: Response): Promise<string> {
  try {
    const body = (await response.json()) as { error?: unknown }
    if (typeof body.error === "string") {
      return body.error
    }
  } catch {
    // Fall through to the status-based message below.
  }
  return `request failed with status ${response.status}`
}

/**
 * Fetch the run list from the dashboard's `/api/v1/runs` proxy route,
 * which serves the same payload as `run list`. `baseUrl` defaults to the
 * empty string, so the request is same-origin from the browser; pass an
 * absolute URL only to target a different dashboard origin (e.g. tests).
 */
async function fetchJson<T>(
  path: string,
  baseUrl: string,
  fetchImpl: typeof fetch
): Promise<T> {
  let response: Response
  try {
    response = await fetchImpl(`${baseUrl}${path}`, { method: "GET" })
  } catch (cause) {
    throw new ApiError("unable to reach the dashboard's API proxy", { cause })
  }
  if (!response.ok) {
    throw new ApiError(await parseErrorBody(response))
  }
  return (await response.json()) as T
}

export async function fetchRunList(
  baseUrl: string = "",
  fetchImpl: typeof fetch = fetch
): Promise<RunSummary[]> {
  return fetchJson<RunSummary[]>("/api/v1/runs", baseUrl, fetchImpl)
}

export async function fetchRunDetailBundle(
  runId: string,
  baseUrl: string = "",
  fetchImpl: typeof fetch = fetch
): Promise<RunDetailBundle> {
  const encodedRunId = encodeURIComponent(runId)
  const runPath = `/api/v1/runs/${encodedRunId}`
  const [detail, history, approvals, usage] = await Promise.all([
    fetchJson<RunDetail>(runPath, baseUrl, fetchImpl),
    fetchJson<RunHistoryEntry[]>(`${runPath}/history`, baseUrl, fetchImpl),
    fetchJson<ApprovalRequest[]>(`${runPath}/approvals`, baseUrl, fetchImpl),
    fetchJson<RunUsage>(`${runPath}/usage`, baseUrl, fetchImpl),
  ])
  return { detail, history, approvals, usage }
}
