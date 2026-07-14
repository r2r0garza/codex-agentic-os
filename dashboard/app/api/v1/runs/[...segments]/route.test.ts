import { afterEach, describe, expect, it, vi } from "vitest"

import { GET, POST } from "./route"

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

function postRequest(body: unknown): Request {
  return new Request("http://dashboard.test", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
  })
}

describe("POST /api/v1/runs/[...segments] proxy", () => {
  it("forwards the run cancel mutation with its JSON body", async () => {
    const fetchImpl = vi
      .fn()
      .mockResolvedValue(Response.json({ run: {}, steps: [] }))
    vi.stubGlobal("fetch", fetchImpl)

    const response = await POST(postRequest({}), {
      params: Promise.resolve({ segments: ["run/001", "cancel"] }),
    })

    expect(response.status).toBe(200)
    expect(fetchImpl).toHaveBeenCalledWith(
      "http://127.0.0.1:8080/api/v1/runs/run%2F001/cancel",
      {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: "{}",
      }
    )
  })

  it.each(["approve", "reject", "retry"])(
    "forwards the step %s mutation with its JSON body",
    async (action) => {
      const fetchImpl = vi
        .fn()
        .mockResolvedValue(Response.json({ run: {}, steps: [] }))
      vi.stubGlobal("fetch", fetchImpl)
      const body = { expected_step_revision: 1, expected_run_revision: 2 }

      const response = await POST(postRequest(body), {
        params: Promise.resolve({
          segments: ["run-001", "steps", "step/1", action],
        }),
      })

      expect(response.status).toBe(200)
      expect(fetchImpl).toHaveBeenCalledWith(
        `http://127.0.0.1:8080/api/v1/runs/run-001/steps/step%2F1/${action}`,
        {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify(body),
        }
      )
    }
  )

  it("rejects unrecognized mutation-shaped paths without forwarding them", async () => {
    const fetchImpl = vi.fn()
    vi.stubGlobal("fetch", fetchImpl)

    const response = await POST(postRequest({}), {
      params: Promise.resolve({ segments: ["run-001", "publish"] }),
    })

    expect(response.status).toBe(404)
    expect(fetchImpl).not.toHaveBeenCalled()
  })

  it("rejects a GET-only evidence path posted as a mutation without forwarding it", async () => {
    const fetchImpl = vi.fn()
    vi.stubGlobal("fetch", fetchImpl)

    const response = await POST(postRequest({}), {
      params: Promise.resolve({ segments: ["run-001", "history"] }),
    })

    expect(response.status).toBe(404)
    expect(fetchImpl).not.toHaveBeenCalled()
  })
})
