import { fireEvent, render, screen, waitFor } from "@testing-library/react"
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

function withFailedSteps(bundle: RunDetailBundle): RunDetailBundle {
  return {
    ...bundle,
    detail: {
      ...bundle.detail,
      steps: [
        ...bundle.detail.steps,
        {
          step_id: "step-failed-eligible",
          run_id: "run-001",
          position: 3,
          objective: "flaky step",
          status: "failed",
          revision: 1,
          command: "<redacted>",
          output: { error: "<redacted>" },
          failure_kind: "execution_error",
          retry_eligible: true,
        },
        {
          step_id: "step-failed-ineligible",
          run_id: "run-001",
          position: 4,
          objective: "unretryable step",
          status: "failed",
          revision: 1,
          command: "<redacted>",
          output: { error: "<redacted>" },
          failure_kind: "timeout",
          retry_eligible: false,
        },
      ],
    },
  }
}

/** Routes GET requests to `bundle` and POST mutations to `postHandlers`, keyed by URL suffix. */
function stubDashboardFetch(
  bundle: RunDetailBundle,
  postHandlers: Record<string, () => Response> = {}
) {
  const fetchImpl = vi
    .fn()
    .mockImplementation((url: string, init?: RequestInit) => {
      if (init?.method === "POST") {
        const entry = Object.entries(postHandlers).find(([suffix]) =>
          url.endsWith(suffix)
        )
        if (entry === undefined) {
          throw new Error(`unexpected POST ${url}`)
        }
        return Promise.resolve(entry[1]())
      }
      return Promise.resolve(responseForUrl(bundle, url))
    })
  vi.stubGlobal("fetch", fetchImpl)
  return fetchImpl
}

afterEach(() => {
  vi.unstubAllGlobals()
})

describe("RunDetail", () => {
  it("renders ordered steps, history, pending approvals, and usage", async () => {
    const bundle = detailBundle()
    stubDashboardFetch(bundle)

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
  })

  it("offers approve/reject only for a pending approval and cancel only for an active run", async () => {
    const bundle = detailBundle("running")
    stubDashboardFetch(bundle)

    render(<RunDetail runId="run-001" onBack={vi.fn()} />)
    await waitFor(() =>
      expect(screen.getByTestId("run-detail")).toBeInTheDocument()
    )

    expect(screen.getByRole("button", { name: "Approve" })).toBeInTheDocument()
    expect(screen.getByRole("button", { name: "Reject" })).toBeInTheDocument()
    expect(
      screen.getByRole("button", { name: "Cancel run" })
    ).toBeInTheDocument()
    expect(
      screen.queryByRole("button", { name: "Retry" })
    ).not.toBeInTheDocument()
  })

  it("hides the cancel control once a run reaches a terminal state", async () => {
    const bundle = detailBundle("succeeded")
    stubDashboardFetch({ ...bundle, approvals: [] })

    render(<RunDetail runId="run-001" onBack={vi.fn()} />)
    await waitFor(() =>
      expect(screen.getByTestId("run-detail")).toBeInTheDocument()
    )

    expect(
      screen.queryByRole("button", { name: "Cancel run" })
    ).not.toBeInTheDocument()
  })

  it("offers retry only for a retry-eligible failed step", async () => {
    const bundle = withFailedSteps(detailBundle())
    stubDashboardFetch(bundle)

    render(<RunDetail runId="run-001" onBack={vi.fn()} />)
    await waitFor(() =>
      expect(screen.getByTestId("run-detail")).toBeInTheDocument()
    )
    expect(screen.getByText("flaky step")).toBeInTheDocument()
    expect(screen.getByText("unretryable step")).toBeInTheDocument()

    expect(screen.getAllByRole("button", { name: "Retry" })).toHaveLength(1)
  })

  it("requires an explicit confirmation before an approve mutation is sent", async () => {
    const bundle = detailBundle()
    const fetchImpl = stubDashboardFetch(bundle, {
      "/approve": () => jsonResponse(bundle.detail),
    })

    render(<RunDetail runId="run-001" onBack={vi.fn()} />)
    await waitFor(() =>
      expect(screen.getByTestId("run-detail")).toBeInTheDocument()
    )

    fireEvent.click(screen.getByRole("button", { name: "Approve" }))
    expect(
      await screen.findByText("Approve this step?")
    ).toBeInTheDocument()
    expect(
      fetchImpl.mock.calls.filter(([, init]) => init?.method === "POST")
    ).toHaveLength(0)

    fireEvent.click(screen.getByRole("button", { name: "Confirm approve" }))

    await waitFor(() =>
      expect(
        fetchImpl.mock.calls.filter(([, init]) => init?.method === "POST")
      ).toHaveLength(1)
    )
  })

  it("refreshes durable state from the API after a successful mutation", async () => {
    const bundle = detailBundle()
    let getCalls = 0
    const fetchImpl = vi.fn().mockImplementation((url: string, init?: RequestInit) => {
      if (init?.method === "POST") {
        return Promise.resolve(jsonResponse(bundle.detail))
      }
      getCalls += 1
      return Promise.resolve(responseForUrl(bundle, url))
    })
    vi.stubGlobal("fetch", fetchImpl)

    render(<RunDetail runId="run-001" onBack={vi.fn()} pollIntervalMs={100_000} />)
    await waitFor(() =>
      expect(screen.getByTestId("run-detail")).toBeInTheDocument()
    )
    const getCallsBeforeMutation = getCalls

    fireEvent.click(screen.getByRole("button", { name: "Approve" }))
    fireEvent.click(
      await screen.findByRole("button", { name: "Confirm approve" })
    )

    await waitFor(() => expect(getCalls).toBeGreaterThan(getCallsBeforeMutation))
    expect(
      screen.queryByTestId("run-detail-mutation-error")
    ).not.toBeInTheDocument()
  })

  it("shows a clean failure message and still refreshes durable state on a stale/ineligible mutation", async () => {
    const bundle = detailBundle()
    const fetchImpl = stubDashboardFetch(bundle, {
      "/approve": () =>
        Response.json(
          { error: "step is not pending approval: step-provider" },
          { status: 409 }
        ),
    })

    render(<RunDetail runId="run-001" onBack={vi.fn()} />)
    await waitFor(() =>
      expect(screen.getByTestId("run-detail")).toBeInTheDocument()
    )

    fireEvent.click(screen.getByRole("button", { name: "Approve" }))
    fireEvent.click(
      await screen.findByRole("button", { name: "Confirm approve" })
    )

    expect(
      await screen.findByTestId("run-detail-mutation-error")
    ).toHaveTextContent("step is not pending approval: step-provider")
    await waitFor(() =>
      expect(
        fetchImpl.mock.calls.some(
          ([url, init]) =>
            init?.method !== "POST" && typeof url === "string" && url.endsWith("/run-001")
        )
      ).toBe(true)
    )
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

  it("never renders declared command/message input, captured output, or raw usage envelopes even when the API response carries them", async () => {
    const sentinel = "SHOULD-NEVER-RENDER-a1b2c3"
    const poisonedBundle = {
      detail: {
        run: {
          run_id: "run-002",
          objective: "review a sensitive mixed run",
          status: "succeeded",
          revision: 1,
          agent_id: "agent-1",
          output: null,
        },
        steps: [
          {
            step_id: "step-command",
            run_id: "run-002",
            position: 1,
            objective: "run a command",
            status: "succeeded",
            revision: 1,
            command: sentinel,
            output: { stdout: sentinel, stderr: sentinel },
          },
          {
            step_id: "step-provider",
            run_id: "run-002",
            position: 2,
            objective: "summarize input",
            status: "succeeded",
            revision: 1,
            message: {
              provider: "openai-compatible",
              model: "demo-model",
              content: sentinel,
              system: sentinel,
            },
            output: { content: sentinel, raw: sentinel },
          },
        ],
      },
      history: [],
      approvals: [],
      usage: {
        run_id: "run-002",
        steps: [
          {
            step_id: "step-provider",
            position: 2,
            status: "succeeded",
            provider: "openai-compatible",
            model: "demo-model",
            usage: {
              available: true,
              input_tokens: 7,
              output_tokens: 3,
              raw: sentinel,
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
    } as unknown as RunDetailBundle

    vi.stubGlobal(
      "fetch",
      vi
        .fn()
        .mockImplementation((url: string) =>
          Promise.resolve(responseForUrl(poisonedBundle, url))
        )
    )

    render(<RunDetail runId="run-002" onBack={vi.fn()} />)

    await waitFor(() =>
      expect(screen.getByTestId("run-detail")).toBeInTheDocument()
    )
    expect(screen.getByText("Provider usage")).toBeInTheDocument()
    expect(screen.queryByText(sentinel)).not.toBeInTheDocument()
    expect(document.body.textContent).not.toContain(sentinel)
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
