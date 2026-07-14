import { describe, expect, it, vi } from "vitest"

import {
  ApiError,
  approveStep,
  cancelRun,
  fetchRunDetailBundle,
  fetchRunList,
  rejectStep,
  retryStep,
} from "@/lib/api"
import type { RunDetail, RunDetailBundle, RunSummary } from "@/lib/api"

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
      { method: "GET" }
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
        jsonResponse({ error: "unknown run" }, { status: 404 })
      )

    await expect(
      fetchRunList("http://127.0.0.1:8080", fetchImpl)
    ).rejects.toThrow(new ApiError("unknown run"))
  })

  it("raises an ApiError when the API is unreachable", async () => {
    const fetchImpl = vi.fn().mockRejectedValue(new TypeError("fetch failed"))

    await expect(
      fetchRunList("http://127.0.0.1:8080", fetchImpl)
    ).rejects.toThrow(ApiError)
  })

  it("defaults to a same-origin relative request, avoiding cross-origin CORS", async () => {
    const fetchImpl = vi.fn().mockResolvedValue(jsonResponse([]))

    await fetchRunList(undefined, fetchImpl)

    expect(fetchImpl).toHaveBeenCalledWith("/api/v1/runs", { method: "GET" })
  })
})

describe("fetchRunDetailBundle", () => {
  it("requests only the four read-only GET contracts for one encoded run id", async () => {
    const bundle: RunDetailBundle = {
      detail: {
        run: {
          run_id: "run/001",
          objective: "inspect a mixed run",
          status: "running",
          revision: 2,
          agent_id: "agent-1",
          output: null,
        },
        steps: [],
      },
      history: [],
      approvals: [],
      usage: {
        run_id: "run/001",
        steps: [],
        aggregate: {
          steps_with_usage_available: 0,
          steps_with_usage_unavailable: 0,
          input_tokens: null,
          output_tokens: null,
        },
      },
    }
    const responses = [
      bundle.detail,
      bundle.history,
      bundle.approvals,
      bundle.usage,
    ]
    const fetchImpl = vi
      .fn()
      .mockImplementation(() =>
        Promise.resolve(jsonResponse(responses.shift()))
      )

    await expect(
      fetchRunDetailBundle("run/001", "http://dashboard.test", fetchImpl)
    ).resolves.toEqual(bundle)

    expect(fetchImpl.mock.calls).toEqual([
      ["http://dashboard.test/api/v1/runs/run%2F001", { method: "GET" }],
      [
        "http://dashboard.test/api/v1/runs/run%2F001/history",
        { method: "GET" },
      ],
      [
        "http://dashboard.test/api/v1/runs/run%2F001/approvals",
        { method: "GET" },
      ],
      ["http://dashboard.test/api/v1/runs/run%2F001/usage", { method: "GET" }],
    ])
  })
})

const mutationOutcome: RunDetail = {
  run: {
    run_id: "run-001",
    objective: "observe a mixed run",
    status: "running",
    revision: 4,
    agent_id: "agent-1",
    output: null,
  },
  steps: [],
}

describe("mutation requests", () => {
  it("approveStep POSTs an empty body to the step approve route", async () => {
    const fetchImpl = vi.fn().mockResolvedValue(jsonResponse(mutationOutcome))

    const result = await approveStep(
      "run/001",
      "step/1",
      "http://dashboard.test",
      fetchImpl
    )

    expect(fetchImpl).toHaveBeenCalledWith(
      "http://dashboard.test/api/v1/runs/run%2F001/steps/step%2F1/approve",
      {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: "{}",
      }
    )
    expect(result).toEqual(mutationOutcome)
  })

  it("rejectStep POSTs an empty body to the step reject route", async () => {
    const fetchImpl = vi.fn().mockResolvedValue(jsonResponse(mutationOutcome))

    await rejectStep("run-001", "step-1", "http://dashboard.test", fetchImpl)

    expect(fetchImpl).toHaveBeenCalledWith(
      "http://dashboard.test/api/v1/runs/run-001/steps/step-1/reject",
      {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: "{}",
      }
    )
  })

  it("cancelRun POSTs an empty body to the run cancel route", async () => {
    const fetchImpl = vi.fn().mockResolvedValue(jsonResponse(mutationOutcome))

    await cancelRun("run-001", "http://dashboard.test", fetchImpl)

    expect(fetchImpl).toHaveBeenCalledWith(
      "http://dashboard.test/api/v1/runs/run-001/cancel",
      {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: "{}",
      }
    )
  })

  it("retryStep POSTs the expected step and run revisions to the retry route", async () => {
    const fetchImpl = vi.fn().mockResolvedValue(jsonResponse(mutationOutcome))

    await retryStep(
      "run-001",
      "step-1",
      2,
      3,
      "http://dashboard.test",
      fetchImpl
    )

    expect(fetchImpl).toHaveBeenCalledWith(
      "http://dashboard.test/api/v1/runs/run-001/steps/step-1/retry",
      {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          expected_step_revision: 2,
          expected_run_revision: 3,
        }),
      }
    )
  })

  it("raises an ApiError carrying the structured 409 body on a stale/ineligible mutation", async () => {
    const fetchImpl = vi
      .fn()
      .mockResolvedValue(
        jsonResponse({ error: "step is not pending approval: step-1" }, { status: 409 })
      )

    await expect(
      approveStep("run-001", "step-1", "http://dashboard.test", fetchImpl)
    ).rejects.toThrow(
      new ApiError("step is not pending approval: step-1")
    )
  })

  it("raises an ApiError when the mutation proxy is unreachable", async () => {
    const fetchImpl = vi.fn().mockRejectedValue(new TypeError("fetch failed"))

    await expect(
      cancelRun("run-001", "http://dashboard.test", fetchImpl)
    ).rejects.toThrow(ApiError)
  })

  it("defaults every mutation to a same-origin relative request", async () => {
    const fetchImpl = vi.fn().mockResolvedValue(jsonResponse(mutationOutcome))

    await approveStep("run-001", "step-1", undefined, fetchImpl)

    expect(fetchImpl).toHaveBeenCalledWith(
      "/api/v1/runs/run-001/steps/step-1/approve",
      expect.objectContaining({ method: "POST" })
    )
  })
})
