"use client"

import * as React from "react"

import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
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
import { usePollingLoad } from "@/hooks/use-polling-load"
import { fetchRunList, type RunStatus } from "@/lib/api"

export const DASHBOARD_POLL_INTERVAL_MS = 2_000

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

export function StatusBadge({ status }: { status: RunStatus }) {
  return <Badge variant={STATUS_BADGE_VARIANT[status]}>{status}</Badge>
}

export function RunList({
  onSelect,
  pollIntervalMs = DASHBOARD_POLL_INTERVAL_MS,
}: {
  onSelect: (runId: string) => void
  pollIntervalMs?: number
}) {
  const load = React.useCallback(() => fetchRunList(), [])
  const { state } = usePollingLoad(load, pollIntervalMs)

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

  if (state.data.length === 0) {
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
          <TableHead className="text-right">Inspect</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {state.data.map((run) => (
          <TableRow key={run.run_id}>
            <TableCell className="font-mono">{run.run_id}</TableCell>
            <TableCell>{run.objective}</TableCell>
            <TableCell>
              <StatusBadge status={run.status} />
            </TableCell>
            <TableCell>{run.agent_id ?? "unassigned"}</TableCell>
            <TableCell className="text-right">
              <Button
                variant="outline"
                size="sm"
                onClick={() => onSelect(run.run_id)}
              >
                View
              </Button>
            </TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  )
}
