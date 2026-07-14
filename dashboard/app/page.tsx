import { RunList } from "@/components/run-list"

export default function Page() {
  return (
    <div className="mx-auto flex min-h-svh max-w-4xl flex-col gap-6 p-6">
      <div>
        <h1 className="text-lg font-medium">Runs</h1>
        <p className="text-sm text-muted-foreground">
          Read-only view of durable runs from the operator API.
        </p>
      </div>
      <RunList />
    </div>
  )
}
