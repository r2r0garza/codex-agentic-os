import { afterEach, describe, expect, it, vi } from "vitest"

import { GET } from "./route"

afterEach(() => {
  vi.unstubAllGlobals()
})

describe("GET /api/v1/runs/[...segments] proxy", () => {
  it.each(["history", "approvals", "usage"])(
    "forwards the read-only %s endpoint with GET",
    async (resource) => {
      const fetchImpl = vi.fn().mockResolvedValue(
        new Response(JSON.stringify([]), {
          status: 200,
          headers: { "content-type": "application/json" },
        })
      )
      vi.stubGlobal("fetch", fetchImpl)

      const response = await GET(new Request("http://dashboard.test"), {
        params: Promise.resolve({ segments: ["run/001", resource] }),
      })

      expect(response.status).toBe(200)
      expect(fetchImpl).toHaveBeenCalledWith(
        `http://127.0.0.1:8080/api/v1/runs/run%2F001/${resource}`,
        { method: "GET" }
      )
    }
  )

  it("forwards run detail through the same read-only boundary", async () => {
    const fetchImpl = vi
      .fn()
      .mockResolvedValue(Response.json({ run: {}, steps: [] }))
    vi.stubGlobal("fetch", fetchImpl)

    await GET(new Request("http://dashboard.test"), {
      params: Promise.resolve({ segments: ["run-001"] }),
    })

    expect(fetchImpl).toHaveBeenCalledWith(
      "http://127.0.0.1:8080/api/v1/runs/run-001",
      { method: "GET" }
    )
  })

  it("rejects unrecognized resources without forwarding them", async () => {
    const fetchImpl = vi.fn()
    vi.stubGlobal("fetch", fetchImpl)

    const response = await GET(new Request("http://dashboard.test"), {
      params: Promise.resolve({ segments: ["run-001", "retry"] }),
    })

    expect(response.status).toBe(404)
    expect(fetchImpl).not.toHaveBeenCalled()
  })
})
