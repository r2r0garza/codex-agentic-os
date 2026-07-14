import { afterEach, describe, expect, it, vi } from "vitest"

import { GET, getBackendBaseUrl } from "./route"

const ORIGINAL_ENV = process.env.API_BASE_URL

afterEach(() => {
  process.env.API_BASE_URL = ORIGINAL_ENV
  vi.unstubAllGlobals()
})

describe("GET /api/v1/runs proxy", () => {
  it("defaults the backend base URL to the documented loopback default", () => {
    delete process.env.API_BASE_URL
    expect(getBackendBaseUrl()).toBe("http://127.0.0.1:8080")
  })

  it("reads an operator-configured backend base URL", () => {
    process.env.API_BASE_URL = "http://127.0.0.1:9000"
    expect(getBackendBaseUrl()).toBe("http://127.0.0.1:9000")
  })

  it("forwards the backend's run list payload and status verbatim", async () => {
    process.env.API_BASE_URL = "http://127.0.0.1:9000"
    const runs = [
      {
        run_id: "run-001",
        objective: "demonstrate a mixed run",
        status: "succeeded",
        revision: 3,
        agent_id: null,
        output: null,
      },
    ]
    const fetchImpl = vi.fn().mockResolvedValue(
      new Response(JSON.stringify(runs), {
        status: 200,
        headers: { "content-type": "application/json" },
      }),
    )
    vi.stubGlobal("fetch", fetchImpl)

    const response = await GET()

    expect(fetchImpl).toHaveBeenCalledWith("http://127.0.0.1:9000/api/v1/runs")
    expect(response.status).toBe(200)
    expect(await response.json()).toEqual(runs)
  })

  it("returns a structured 502 when the backend is unreachable", async () => {
    process.env.API_BASE_URL = "http://127.0.0.1:9000"
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("fetch failed")))

    const response = await GET()

    expect(response.status).toBe(502)
    expect(await response.json()).toEqual({
      error: "unable to reach the API at http://127.0.0.1:9000",
    })
  })
})
