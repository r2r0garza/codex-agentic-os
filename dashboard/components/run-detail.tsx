"use client"

import * as React from "react"

import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
  AlertDialogTrigger,
} from "@/components/ui/alert-dialog"
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert"
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
import {
  ApiError,
  approveStep,
  cancelRun,
  fetchRunDetailBundle,
  rejectStep,
  retryStep,
  type RunStatus,
} from "@/lib/api"
import { DASHBOARD_POLL_INTERVAL_MS, StatusBadge } from "./run-list"

function displayTokens(value: number | null): string {
  return value === null ? "unavailable" : value.toLocaleString()
}

const ACTIVE_RUN_STATUSES: ReadonlySet<RunStatus> = new Set([
  "queued",
  "running",
])

/**
 * A button that requires an explicit confirmation before firing `onConfirm`.
 * Disabled (both trigger and dialog action) whenever another mutation for
 * this run is already in flight, so competing dashboard actions cannot
 * double-execute a step.
 */
function ConfirmMutationButton({
  label,
  confirmTitle,
  confirmDescription,
  variant = "outline",
  disabled = false,
  onConfirm,
}: {
  label: string
  confirmTitle: string
  confirmDescription: string
  variant?: "outline" | "destructive" | "secondary"
  disabled?: boolean
  onConfirm: () => Promise<void>
}) {
  const [open, setOpen] = React.useState(false)
  const [submitting, setSubmitting] = React.useState(false)

  async function handleConfirm() {
    setSubmitting(true)
    try {
      await onConfirm()
    } finally {
      setSubmitting(false)
      setOpen(false)
    }
  }

  return (
    <AlertDialog open={open} onOpenChange={setOpen}>
      <AlertDialogTrigger
        render={<Button size="sm" variant={variant} disabled={disabled} />}
      >
        {label}
      </AlertDialogTrigger>
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>{confirmTitle}</AlertDialogTitle>
          <AlertDialogDescription>{confirmDescription}</AlertDialogDescription>
        </AlertDialogHeader>
        <AlertDialogFooter>
          <AlertDialogCancel>Back</AlertDialogCancel>
          <AlertDialogAction
            variant={variant}
            disabled={submitting}
            onClick={handleConfirm}
          >
            {submitting ? "Working…" : `Confirm ${label.toLowerCase()}`}
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  )
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
  const { state, refresh } = usePollingLoad(load, pollIntervalMs)
  const [mutatingId, setMutatingId] = React.useState<string | null>(null)
  const [mutationError, setMutationError] = React.useState<string | null>(null)

  async function runMutation(id: string, action: () => Promise<unknown>) {
    setMutatingId(id)
    setMutationError(null)
    try {
      await action()
    } catch (error) {
      setMutationError(
        error instanceof ApiError ? error.message : "mutation failed"
      )
    } finally {
      setMutatingId(null)
      refresh()
    }
  }

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

  const runIsActive = ACTIVE_RUN_STATUSES.has(detail.run.status)
  const anyMutationInFlight = mutatingId !== null

  return (
    <div className="flex flex-col gap-6" data-testid="run-detail">
      {mutationError !== null ? (
        <Alert variant="destructive" data-testid="run-detail-mutation-error">
          <AlertTitle>Action failed</AlertTitle>
          <AlertDescription>{mutationError}</AlertDescription>
        </Alert>
      ) : null}

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
        <div className="flex items-center gap-3">
          <div data-testid="run-detail-status">
            <StatusBadge status={detail.run.status} />
          </div>
          {runIsActive ? (
            <ConfirmMutationButton
              label="Cancel run"
              confirmTitle="Cancel this run?"
              confirmDescription="The run and any queued or running steps will move to cancelled. This cannot be undone."
              variant="destructive"
              disabled={anyMutationInFlight}
              onConfirm={() =>
                runMutation(detail.run.run_id, () => cancelRun(detail.run.run_id))
              }
            />
          ) : null}
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
                  <TableHead className="text-right">Actions</TableHead>
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
                    <TableCell className="text-right">
                      {step.status === "failed" && step.retry_eligible === true ? (
                        <ConfirmMutationButton
                          label="Retry"
                          confirmTitle="Retry this failed step?"
                          confirmDescription="A new step attempt will be queued from this failure. The original step record is kept for history."
                          variant="secondary"
                          disabled={anyMutationInFlight}
                          onConfirm={() =>
                            runMutation(step.step_id, () =>
                              retryStep(
                                detail.run.run_id,
                                step.step_id,
                                step.revision,
                                detail.run.revision
                              )
                            )
                          }
                        />
                      ) : null}
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
                  <div className="flex flex-wrap items-center justify-between gap-3">
                    <p className="text-sm font-medium">{approval.objective}</p>
                    <div className="flex items-center gap-2">
                      <Badge variant="secondary">pending</Badge>
                      <ConfirmMutationButton
                        label="Approve"
                        confirmTitle="Approve this step?"
                        confirmDescription="The step becomes eligible for normal execution on its next dispatch."
                        variant="outline"
                        disabled={anyMutationInFlight}
                        onConfirm={() =>
                          runMutation(approval.step_id, () =>
                            approveStep(approval.run_id, approval.step_id)
                          )
                        }
                      />
                      <ConfirmMutationButton
                        label="Reject"
                        confirmTitle="Reject this step?"
                        confirmDescription="The step and its run move to a terminal failed state without executing. This cannot be undone."
                        variant="destructive"
                        disabled={anyMutationInFlight}
                        onConfirm={() =>
                          runMutation(approval.step_id, () =>
                            rejectStep(approval.run_id, approval.step_id)
                          )
                        }
                      />
                    </div>
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
