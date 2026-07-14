import { proxyMutationPost, proxyReadOnlyGet } from "../../proxy"

const EVIDENCE_RESOURCES = new Set(["history", "approvals", "usage"])
const STEP_MUTATIONS = new Set(["approve", "reject", "retry"])

export async function GET(
  _request: Request,
  { params }: { params: Promise<{ segments: string[] }> }
) {
  const { segments } = await params
  if (
    segments.length < 1 ||
    segments.length > 2 ||
    (segments.length === 2 && !EVIDENCE_RESOURCES.has(segments[1]))
  ) {
    return Response.json(
      { error: "unrecognized dashboard API path" },
      { status: 404 }
    )
  }
  const path = segments.map(encodeURIComponent).join("/")
  return proxyReadOnlyGet(`/api/v1/runs/${path}`)
}

async function readJsonBody(request: Request): Promise<unknown> {
  const raw = await request.text()
  if (!raw.trim()) {
    return {}
  }
  try {
    return JSON.parse(raw)
  } catch {
    return { __unparseable__: raw }
  }
}

/**
 * Forward only the four mutation-shaped paths #117 added to the backend:
 * `runs/{run_id}/cancel` and `runs/{run_id}/steps/{step_id}/{approve,reject,retry}`.
 * Every other path is rejected without forwarding, matching the `GET`
 * handler's unrecognized-path behavior above.
 */
export async function POST(
  request: Request,
  { params }: { params: Promise<{ segments: string[] }> }
) {
  const { segments } = await params
  const body = await readJsonBody(request)

  if (segments.length === 2 && segments[1] === "cancel") {
    const path = `${encodeURIComponent(segments[0])}/cancel`
    return proxyMutationPost(`/api/v1/runs/${path}`, body)
  }

  if (
    segments.length === 4 &&
    segments[1] === "steps" &&
    STEP_MUTATIONS.has(segments[3])
  ) {
    const path = `${encodeURIComponent(segments[0])}/steps/${encodeURIComponent(
      segments[2]
    )}/${segments[3]}`
    return proxyMutationPost(`/api/v1/runs/${path}`, body)
  }

  return Response.json(
    { error: "unrecognized dashboard API path" },
    { status: 404 }
  )
}
