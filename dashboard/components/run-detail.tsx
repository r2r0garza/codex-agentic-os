"use client"

import * as React from "react"

import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
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
import { fetchRunDetailBundle } from "@/lib/api"
import { DASHBOARD_POLL_INTERVAL_MS, StatusBadge } from "./run-list"

function displayTokens(value: number | null): string {
  return value === null ? "unavailable" : value.toLocaleString()
}

export function RunDetail({
  runId,
  onBack,
  pollIntervalMs = DASHBOARD_POLL_INTERVAL_MS,
}: {
  runId: string
  onBack: () => void
  pollIntervalMs?: number
}) {
  const load = React.useCallback(() => fetchRunDetailBundle(runId), [runId])
  const state = usePollingLoad(load, pollIntervalMs)

  if (state.kind === "loading") {
    return (
      <div className="flex flex-col gap-3" data-testid="run-detail-loading">
        <Skeleton className="h-9 w-32" />
        <Skeleton className="h-28 w-full" />
        <Skeleton className="h-48 w-full" />
      </div>
    )
  }

  if (state.kind === "error") {
    return (
      <div className="flex flex-col gap-4">
        <Button className="w-fit" variant="outline" onClick={onBack}>
          Back to runs
        </Button>
        <Empty data-testid="run-detail-error">
          <EmptyHeader>
            <EmptyTitle>Unable to reach the API</EmptyTitle>
            <EmptyDescription>{state.message}</EmptyDescription>
          </EmptyHeader>
        </Empty>
      </div>
    )
  }

  const { detail, history, approvals, usage } = state.data
  const pendingApprovals = approvals.filter(
    (approval) => approval.approval_status === "pending"
  )

  return (
    <div className="flex flex-col gap-6" data-testid="run-detail">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div className="space-y-1">
          <Button className="mb-2" variant="outline" size="sm" onClick={onBack}>
            Back to runs
          </Button>
          <h1 className="font-mono text-lg font-medium">{detail.run.run_id}</h1>
          <p className="text-sm text-muted-foreground">
            {detail.run.objective}
          </p>
        </div>
        <div data-testid="run-detail-status">
          <StatusBadge status={detail.run.status} />
        </div>
      </div>

      <Card>
        <CardHeader>
          <CardTitle>Ordered steps</CardTitle>
        </CardHeader>
        <CardContent>
          {detail.steps.length === 0 ? (
            <p className="text-sm text-muted-foreground">No steps recorded.</p>
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Position</TableHead>
                  <TableHead>Objective</TableHead>
                  <TableHead>Kind</TableHead>
                  <TableHead>Status</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {detail.steps.map((step) => (
                  <TableRow key={step.step_id}>
                    <TableCell>{step.position}</TableCell>
                    <TableCell>{step.objective}</TableCell>
                    <TableCell>
                      {step.message ? "provider" : "command"}
                    </TableCell>
                    <TableCell>
                      <StatusBadge status={step.status} />
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Lifecycle history</CardTitle>
        </CardHeader>
        <CardContent>
          {history.length === 0 ? (
            <p className="text-sm text-muted-foreground">
              No history recorded.
            </p>
          ) : (
            <div className="space-y-3">
              {history.map((entry) => (
                <div className="flex items-start gap-3" key={entry.sequence}>
                  <Badge variant="outline">{entry.sequence}</Badge>
                  <div>
                    <p className="text-sm font-medium">{entry.transition}</p>
                    <p className="text-xs text-muted-foreground">
                      {entry.status}
                      {entry.step_id ? ` · ${entry.step_id}` : ""}
                      {entry.agent_id ? ` · ${entry.agent_id}` : ""}
                    </p>
                  </div>
                </div>
              ))}
            </div>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Pending approvals</CardTitle>
        </CardHeader>
        <CardContent>
          {pendingApprovals.length === 0 ? (
            <p className="text-sm text-muted-foreground">
              No pending approvals.
            </p>
          ) : (
            <div className="space-y-3">
              {pendingApprovals.map((approval) => (
                <div className="rounded-md border p-3" key={approval.step_id}>
                  <div className="flex items-center justify-between gap-3">
                    <p className="text-sm font-medium">{approval.objective}</p>
                    <Badge variant="secondary">pending</Badge>
                  </div>
                  <p className="mt-1 text-xs text-muted-foreground">
                    Step {approval.position} · {approval.execution_kind} ·{" "}
                    {approval.step_id}
                  </p>
                </div>
              ))}
            </div>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Provider usage</CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="grid gap-3 sm:grid-cols-2">
            <div className="rounded-md border p-3">
              <p className="text-xs text-muted-foreground">Input tokens</p>
              <p className="text-lg font-medium">
                {displayTokens(usage.aggregate.input_tokens)}
              </p>
            </div>
            <div className="rounded-md border p-3">
              <p className="text-xs text-muted-foreground">Output tokens</p>
              <p className="text-lg font-medium">
                {displayTokens(usage.aggregate.output_tokens)}
              </p>
            </div>
          </div>
          {usage.steps.length === 0 ? (
            <p className="text-sm text-muted-foreground">
              No provider steps recorded.
            </p>
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Step</TableHead>
                  <TableHead>Provider</TableHead>
                  <TableHead>Model</TableHead>
                  <TableHead>Input</TableHead>
                  <TableHead>Output</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {usage.steps.map((step) => (
                  <TableRow key={step.step_id}>
                    <TableCell>{step.step_id}</TableCell>
                    <TableCell>{step.provider}</TableCell>
                    <TableCell>{step.model ?? "default"}</TableCell>
                    <TableCell>
                      {displayTokens(step.usage.input_tokens)}
                    </TableCell>
                    <TableCell>
                      {displayTokens(step.usage.output_tokens)}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
        </CardContent>
      </Card>
    </div>
  )
}
