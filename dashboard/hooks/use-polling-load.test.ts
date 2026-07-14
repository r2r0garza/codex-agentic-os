import { renderHook, waitFor } from "@testing-library/react"
import { act } from "react"
import { describe, expect, it, vi } from "vitest"

import { usePollingLoad } from "./use-polling-load"

describe("usePollingLoad", () => {
  it("refresh() reloads immediately without waiting for the poll interval", async () => {
    let calls = 0
    const load = vi.fn().mockImplementation(() => {
      calls += 1
      return Promise.resolve(calls)
    })

    const { result } = renderHook(() => usePollingLoad(load, 1_000_000))

    await waitFor(() => expect(result.current.state).toEqual({
      kind: "ready",
      data: 1,
    }))

    act(() => {
      result.current.refresh()
    })

    await waitFor(() => expect(result.current.state).toEqual({
      kind: "ready",
      data: 2,
    }))
    expect(calls).toBe(2)
  })
})
