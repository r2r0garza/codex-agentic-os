import { render, screen, waitFor } from "@testing-library/react"
import { afterEach, describe, expect, it, vi } from "vitest"

import { RunDetail } from "@/components/run-detail"
import type { RunDetailBundle } from "@/lib/api"

function jsonResponse(body: unknown): Response {
  return Response.json(body)
}

function detailBundle(
  status: "running" | "succeeded" = "running"
): RunDetailBundle {
  return {
    detail: {
      run: {
        run_id: "run-001",
        objective: "observe a mixed run",
        status,
        revision: 3,
        agent_id: "agent-1",
        output: null,
      },
      steps: [
        {
          step_id: "step-command",
          run_id: "run-001",
          position: 1,
          objective: "prepare input",
          status: "succeeded",
          revision: 2,
          command: "<redacted>",
          output: null,
        },
        {
          step_id: "step-provider",
          run_id: "run-001",
          position: 2,
          objective: "summarize input",
          status: "queued",
          revision: 1,
          message: { provider: "openai-compatible", model: "demo-model" },
          output: null,
        },
      ],
    },
    history: [
      {
        run_id: "run-001",
        sequence: 1,
        transition: "created",
        status: "queued",
        agent_id: "agent-1",
        execution_kind: null,
        step_id: null,
      },
    ],
    approvals: [
      {
        step_id: "step-provider",
        run_id: "run-001",
        position: 2,
        objective: "summarize input",
        step_status: "queued",
        approval_required: true,
        approval_status: "pending",
        execution_kind: "provider",
        requesting_agent_id: "agent-1",
        deciding_agent_id: null,
      },
    ],
    usage: {
      run_id: "run-001",
      steps: [
        {
          step_id: "step-provider",
          position: 2,
          status: "queued",
          provider: "openai-compatible",
          model: "demo-model",
          usage: {
            available: true,
            input_tokens: 7,
            output_tokens: 3,
            raw: null,
            unavailable_reason: null,
          },
        },
      ],
      aggregate: {
        steps_with_usage_available: 1,
        steps_with_usage_unavailable: 0,
        input_tokens: 7,
        output_tokens: 3,
      },
    },
  }
}

function responseForUrl(bundle: RunDetailBundle, url: string): Response {
  if (url.endsWith("/history")) return jsonResponse(bundle.history)
  if (url.endsWith("/approvals")) return jsonResponse(bundle.approvals)
  if (url.endsWith("/usage")) return jsonResponse(bundle.usage)
  return jsonResponse(bundle.detail)
}

afterEach(() => {
  vi.unstubAllGlobals()
})

describe("RunDetail", () => {
  it("renders ordered steps, history, pending approvals, and usage without mutation controls", async () => {
    const bundle = detailBundle()
    vi.stubGlobal(
      "fetch",
      vi
        .fn()
        .mockImplementation((url: string) =>
          Promise.resolve(responseForUrl(bundle, url))
        )
    )

    render(<RunDetail runId="run-001" onBack={vi.fn()} />)

    await waitFor(() =>
      expect(screen.getByTestId("run-detail")).toBeInTheDocument()
    )
    expect(screen.getByText("Ordered steps")).toBeInTheDocument()
    expect(screen.getByText("prepare input")).toBeInTheDocument()
    expect(screen.getByText("Lifecycle history")).toBeInTheDocument()
    expect(screen.getByText("created")).toBeInTheDocument()
    expect(screen.getByText("Pending approvals")).toBeInTheDocument()
    expect(screen.getAllByText("summarize input").length).toBeGreaterThan(0)
    expect(screen.getByText("Provider usage")).toBeInTheDocument()
    expect(screen.getByText("demo-model")).toBeInTheDocument()
    expect(
      screen.queryByRole("button", { name: /approve|reject|cancel|retry/i })
    ).not.toBeInTheDocument()
  })

  it("refreshes the rendered run state by non-overlapping polling", async () => {
    let detailRequests = 0
    vi.stubGlobal(
      "fetch",
      vi.fn().mockImplementation((url: string) => {
        if (
          !url.endsWith("/history") &&
          !url.endsWith("/approvals") &&
          !url.endsWith("/usage")
        ) {
          detailRequests += 1
        }
        const bundle = detailBundle(
          detailRequests > 1 ? "succeeded" : "running"
        )
        return Promise.resolve(responseForUrl(bundle, url))
      })
    )

    render(<RunDetail runId="run-001" onBack={vi.fn()} pollIntervalMs={10} />)

    await waitFor(() =>
      expect(screen.getByTestId("run-detail-status")).toHaveTextContent(
        "running"
      )
    )
    await waitFor(() =>
      expect(screen.getByTestId("run-detail-status")).toHaveTextContent(
        "succeeded"
      )
    )
    expect(detailRequests).toBeGreaterThanOrEqual(2)
  })

  it("replaces stale detail with an explicit error after a failed refresh", async () => {
    const bundle = detailBundle()
    let requests = 0
    vi.stubGlobal(
      "fetch",
      vi.fn().mockImplementation((url: string) => {
        requests += 1
        if (requests > 4) {
          return Promise.reject(new TypeError("fetch failed"))
        }
        return Promise.resolve(responseForUrl(bundle, url))
      })
    )

    render(<RunDetail runId="run-001" onBack={vi.fn()} pollIntervalMs={10} />)

    await waitFor(() =>
      expect(screen.getByTestId("run-detail")).toBeInTheDocument()
    )
    await waitFor(() =>
      expect(screen.getByTestId("run-detail-error")).toBeInTheDocument()
    )
    expect(screen.queryByTestId("run-detail")).not.toBeInTheDocument()
    expect(screen.getByText("Unable to reach the API")).toBeInTheDocument()
  })
})
