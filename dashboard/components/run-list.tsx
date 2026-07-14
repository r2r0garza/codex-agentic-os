"use client"

import * as React from "react"

import { Badge } from "@/components/ui/badge"
import {
  Empty,
  EmptyDescription,
  EmptyHeader,
  EmptyTitle,
} from "@/components/ui/empty"
import { Skeleton } from "@/components/ui/skeleton"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import { ApiError, fetchRunList, type RunStatus, type RunSummary } from "@/lib/api"

const STATUS_BADGE_VARIANT: Record<
  RunStatus,
  "default" | "secondary" | "destructive" | "outline"
> = {
  queued: "outline",
  running: "secondary",
  succeeded: "default",
  failed: "destructive",
  cancelled: "outline",
}

type LoadState =
  | { kind: "loading" }
  | { kind: "error"; message: string }
  | { kind: "ready"; runs: RunSummary[] }

export function RunList() {
  const [state, setState] = React.useState<LoadState>({ kind: "loading" })

  React.useEffect(() => {
    let cancelled = false

    fetchRunList()
      .then((runs) => {
        if (!cancelled) {
          setState({ kind: "ready", runs })
        }
      })
      .catch((error: unknown) => {
        if (cancelled) {
          return
        }
        const message =
          error instanceof ApiError ? error.message : "unable to load runs"
        setState({ kind: "error", message })
      })

    return () => {
      cancelled = true
    }
  }, [])

  if (state.kind === "loading") {
    return (
      <div className="flex flex-col gap-2" data-testid="run-list-loading">
        <Skeleton className="h-8 w-full" />
        <Skeleton className="h-8 w-full" />
        <Skeleton className="h-8 w-full" />
      </div>
    )
  }

  if (state.kind === "error") {
    return (
      <Empty data-testid="run-list-error">
        <EmptyHeader>
          <EmptyTitle>Unable to reach the API</EmptyTitle>
          <EmptyDescription>{state.message}</EmptyDescription>
        </EmptyHeader>
      </Empty>
    )
  }

  if (state.runs.length === 0) {
    return (
      <Empty data-testid="run-list-empty">
        <EmptyHeader>
          <EmptyTitle>No runs yet</EmptyTitle>
          <EmptyDescription>
            Durable runs will appear here once created.
          </EmptyDescription>
        </EmptyHeader>
      </Empty>
    )
  }

  return (
    <Table data-testid="run-list-table">
      <TableHeader>
        <TableRow>
          <TableHead>Run</TableHead>
          <TableHead>Objective</TableHead>
          <TableHead>Status</TableHead>
          <TableHead>Agent</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {state.runs.map((run) => (
          <TableRow key={run.run_id}>
            <TableCell className="font-mono">{run.run_id}</TableCell>
            <TableCell>{run.objective}</TableCell>
            <TableCell>
              <Badge variant={STATUS_BADGE_VARIANT[run.status]}>
                {run.status}
              </Badge>
            </TableCell>
            <TableCell>{run.agent_id ?? "unassigned"}</TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  )
}
