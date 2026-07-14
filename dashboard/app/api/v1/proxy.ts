const DEFAULT_BACKEND_BASE_URL = "http://127.0.0.1:8080"

export function getBackendBaseUrl(): string {
  return process.env.API_BASE_URL || DEFAULT_BACKEND_BASE_URL
}

export async function proxyReadOnlyGet(path: string): Promise<Response> {
  const backendBaseUrl = getBackendBaseUrl()
  let response: Response
  try {
    response = await fetch(`${backendBaseUrl}${path}`, { method: "GET" })
  } catch {
    return Response.json(
      { error: `unable to reach the API at ${backendBaseUrl}` },
      { status: 502 }
    )
  }
  const body = await response.text()
  return new Response(body, {
    status: response.status,
    headers: {
      "content-type":
        response.headers.get("content-type") ?? "application/json",
    },
  })
}

/**
 * Forward one JSON request body to a `POST` mutation route on the backend.
 * Only called for the explicitly enumerated mutation paths in
 * `runs/[...segments]/route.ts`; every other path stays on
 * `proxyReadOnlyGet`.
 */
export async function proxyMutationPost(
  path: string,
  body: unknown
): Promise<Response> {
  const backendBaseUrl = getBackendBaseUrl()
  let response: Response
  try {
    response = await fetch(`${backendBaseUrl}${path}`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(body ?? {}),
    })
  } catch {
    return Response.json(
      { error: `unable to reach the API at ${backendBaseUrl}` },
      { status: 502 }
    )
  }
  const responseBody = await response.text()
  return new Response(responseBody, {
    status: response.status,
    headers: {
      "content-type":
        response.headers.get("content-type") ?? "application/json",
    },
  })
}
