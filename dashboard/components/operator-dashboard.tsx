"use client"

import * as React from "react"

import { RunDetail } from "@/components/run-detail"
import { RunList } from "@/components/run-list"

export function OperatorDashboard() {
  const [selectedRunId, setSelectedRunId] = React.useState<string | null>(null)

  if (selectedRunId !== null) {
    return (
      <RunDetail runId={selectedRunId} onBack={() => setSelectedRunId(null)} />
    )
  }

  return (
    <div className="flex flex-col gap-6">
      <div>
        <h1 className="text-lg font-medium">Runs</h1>
        <p className="text-sm text-muted-foreground">
          Read-only view of durable runs from the operator API.
        </p>
      </div>
      <RunList onSelect={setSelectedRunId} />
    </div>
  )
}
