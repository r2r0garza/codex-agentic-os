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

export type RunStatus = "queued" | "running" | "succeeded" | "failed" | "cancelled"

export interface RunSummary {
  run_id: string
  objective: string
  status: RunStatus
  revision: number
  agent_id: string | null
  output: Record<string, unknown> | null
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
export async function fetchRunList(
  baseUrl: string = "",
  fetchImpl: typeof fetch = fetch,
): Promise<RunSummary[]> {
  let response: Response
  try {
    response = await fetchImpl(`${baseUrl}/api/v1/runs`)
  } catch (cause) {
    throw new ApiError("unable to reach the dashboard's API proxy", { cause })
  }
  if (!response.ok) {
    throw new ApiError(await parseErrorBody(response))
  }
  return (await response.json()) as RunSummary[]
}
