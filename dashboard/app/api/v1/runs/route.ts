/**
 * Same-origin proxy for `GET /api/v1/runs` on the codex-agentic-os
 * read-only loopback HTTP API. Runs server-side (unaffected by the
 * browser's CORS policy) and forwards the backend's status and body
 * verbatim — no reinterpretation, redaction, or mutation of any kind.
 */

const DEFAULT_BACKEND_BASE_URL = "http://127.0.0.1:8080"

export function getBackendBaseUrl(): string {
  return process.env.API_BASE_URL || DEFAULT_BACKEND_BASE_URL
}

export async function GET() {
  const backendBaseUrl = getBackendBaseUrl()
  let response: Response
  try {
    response = await fetch(`${backendBaseUrl}/api/v1/runs`)
  } catch {
    return Response.json(
      { error: `unable to reach the API at ${backendBaseUrl}` },
      { status: 502 },
    )
  }
  const body = await response.text()
  return new Response(body, {
    status: response.status,
    headers: {
      "content-type": response.headers.get("content-type") ?? "application/json",
    },
  })
}
