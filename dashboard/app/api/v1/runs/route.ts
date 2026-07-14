/**
 * Same-origin proxy for `GET /api/v1/runs` on the codex-agentic-os
 * read-only loopback HTTP API. Runs server-side (unaffected by the
 * browser's CORS policy) and forwards the backend's status and body
 * verbatim — no reinterpretation, redaction, or mutation of any kind.
 */

import { getBackendBaseUrl, proxyReadOnlyGet } from "../proxy"

export { getBackendBaseUrl }

export async function GET() {
  return proxyReadOnlyGet("/api/v1/runs")
}
