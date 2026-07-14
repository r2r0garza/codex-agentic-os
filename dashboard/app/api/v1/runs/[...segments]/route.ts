import { proxyReadOnlyGet } from "../../proxy"

const EVIDENCE_RESOURCES = new Set(["history", "approvals", "usage"])

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
