"use client"

import * as React from "react"

import { ApiError } from "@/lib/api"

export type LoadState<T> =
  | { kind: "loading" }
  | { kind: "error"; message: string }
  | { kind: "ready"; data: T }

export function usePollingLoad<T>(
  load: () => Promise<T>,
  intervalMs: number
): LoadState<T> {
  const [state, setState] = React.useState<LoadState<T>>({ kind: "loading" })

  React.useEffect(() => {
    let cancelled = false
    let timeout: ReturnType<typeof setTimeout> | undefined

    async function poll() {
      try {
        const data = await load()
        if (!cancelled) {
          setState({ kind: "ready", data })
        }
      } catch (error: unknown) {
        if (!cancelled) {
          const message =
            error instanceof ApiError
              ? error.message
              : "unable to load API data"
          setState({ kind: "error", message })
        }
      } finally {
        if (!cancelled) {
          timeout = setTimeout(poll, intervalMs)
        }
      }
    }

    void poll()
    return () => {
      cancelled = true
      if (timeout !== undefined) {
        clearTimeout(timeout)
      }
    }
  }, [intervalMs, load])

  return state
}
