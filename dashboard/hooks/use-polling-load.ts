"use client"

import * as React from "react"

import { ApiError } from "@/lib/api"

export type LoadState<T> =
  | { kind: "loading" }
  | { kind: "error"; message: string }
  | { kind: "ready"; data: T }

export interface PollingLoad<T> {
  state: LoadState<T>
  /** Trigger an immediate reload outside the regular polling cadence. */
  refresh: () => void
}

export function usePollingLoad<T>(
  load: () => Promise<T>,
  intervalMs: number
): PollingLoad<T> {
  const [state, setState] = React.useState<LoadState<T>>({ kind: "loading" })
  const pollNowRef = React.useRef<() => void>(() => {})

  React.useEffect(() => {
    let cancelled = false
    let timeout: ReturnType<typeof setTimeout> | undefined

    async function poll() {
      if (timeout !== undefined) {
        clearTimeout(timeout)
      }
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

    pollNowRef.current = () => void poll()
    void poll()
    return () => {
      cancelled = true
      if (timeout !== undefined) {
        clearTimeout(timeout)
      }
    }
  }, [intervalMs, load])

  const refresh = React.useCallback(() => {
    pollNowRef.current()
  }, [])

  return { state, refresh }
}
