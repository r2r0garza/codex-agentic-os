import { render, screen, waitFor } from "@testing-library/react"
import { afterEach, describe, expect, it, vi } from "vitest"

import { RunList } from "@/components/run-list"
import type { RunSummary } from "@/lib/api"

function jsonResponse(body: unknown, init: ResponseInit = {}): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { "content-type": "application/json" },
    ...init,
  })
}

afterEach(() => {
  vi.unstubAllGlobals()
})

describe("RunList", () => {
  it("shows a loading state before the API responds", () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockReturnValue(new Promise(() => {})),
    )

    render(<RunList />)

    expect(screen.getByTestId("run-list-loading")).toBeInTheDocument()
  })

  it("renders durable runs and their statuses once loaded", async () => {
    const runs: RunSummary[] = [
      {
        run_id: "run-001",
        objective: "demonstrate a mixed run",
        status: "running",
        revision: 2,
        agent_id: "agent-1",
        output: null,
      },
      {
        run_id: "run-002",
        objective: "queued command run",
        status: "queued",
        revision: 0,
        agent_id: null,
        output: null,
      },
    ]
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonResponse(runs)))

    render(<RunList />)

    await waitFor(() =>
      expect(screen.getByTestId("run-list-table")).toBeInTheDocument(),
    )

    expect(screen.getByText("run-001")).toBeInTheDocument()
    expect(screen.getByText("running")).toBeInTheDocument()
    expect(screen.getByText("run-002")).toBeInTheDocument()
    expect(screen.getByText("unassigned")).toBeInTheDocument()
  })

  it("shows an explicit empty state without fabricating rows", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonResponse([])))

    render(<RunList />)

    await waitFor(() =>
      expect(screen.getByTestId("run-list-empty")).toBeInTheDocument(),
    )
    expect(screen.queryByTestId("run-list-table")).not.toBeInTheDocument()
  })

  it("shows an explicit error state when the API is unreachable", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockRejectedValue(new TypeError("fetch failed")),
    )

    render(<RunList />)

    await waitFor(() =>
      expect(screen.getByTestId("run-list-error")).toBeInTheDocument(),
    )
    expect(screen.queryByTestId("run-list-table")).not.toBeInTheDocument()
  })
})
