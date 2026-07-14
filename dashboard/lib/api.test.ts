import { describe, expect, it, vi } from "vitest"

import { ApiError, fetchRunList } from "@/lib/api"
import type { RunSummary } from "@/lib/api"

function jsonResponse(body: unknown, init: ResponseInit = {}): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { "content-type": "application/json" },
    ...init,
  })
}

describe("fetchRunList", () => {
  it("maps the run list payload from GET /api/v1/runs", async () => {
    const runs: RunSummary[] = [
      {
        run_id: "run-001",
        objective: "demonstrate a mixed run",
        status: "succeeded",
        revision: 3,
        agent_id: "agent-1",
        output: null,
      },
    ]
    const fetchImpl = vi.fn().mockResolvedValue(jsonResponse(runs))

    const result = await fetchRunList("http://127.0.0.1:8080", fetchImpl)

    expect(fetchImpl).toHaveBeenCalledWith(
      "http://127.0.0.1:8080/api/v1/runs",
    )
    expect(result).toEqual(runs)
  })

  it("returns an empty list without fabricating rows", async () => {
    const fetchImpl = vi.fn().mockResolvedValue(jsonResponse([]))

    const result = await fetchRunList("http://127.0.0.1:8080", fetchImpl)

    expect(result).toEqual([])
  })

  it("raises an ApiError carrying the structured error body on a non-OK response", async () => {
    const fetchImpl = vi
      .fn()
      .mockResolvedValue(
        jsonResponse({ error: "unknown run" }, { status: 404 }),
      )

    await expect(
      fetchRunList("http://127.0.0.1:8080", fetchImpl),
    ).rejects.toThrow(new ApiError("unknown run"))
  })

  it("raises an ApiError when the API is unreachable", async () => {
    const fetchImpl = vi.fn().mockRejectedValue(new TypeError("fetch failed"))

    await expect(
      fetchRunList("http://127.0.0.1:8080", fetchImpl),
    ).rejects.toThrow(ApiError)
  })

  it("defaults to a same-origin relative request, avoiding cross-origin CORS", async () => {
    const fetchImpl = vi.fn().mockResolvedValue(jsonResponse([]))

    await fetchRunList(undefined, fetchImpl)

    expect(fetchImpl).toHaveBeenCalledWith("/api/v1/runs")
  })
})
