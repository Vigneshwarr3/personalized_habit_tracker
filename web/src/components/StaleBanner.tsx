/** "Offline, showing data from 10:42": shown when a refresh failed but the page still has saved data. */
import { useSyncExternalStore } from "react"
import { useQueryClient, type Query } from "@tanstack/react-query"
import { CloudOff, RotateCw } from "lucide-react"
import { Button } from "@/components/ui/button"
import { errorKind } from "@/lib/queryClient"

const stale = (q: Query) => q.state.status === "error" && q.state.data !== undefined && q.getObserversCount() > 0

function when(ms: number): string {
  const d = new Date(ms)
  const time = d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })
  return d.toDateString() === new Date().toDateString()
    ? time
    : `${d.toLocaleDateString([], { month: "short", day: "numeric" })}, ${time}`
}

export function StaleBanner() {
  const qc = useQueryClient()
  const cache = qc.getQueryCache()
  // the oldest saved time among pages on screen whose refresh failed (0 = none)
  const savedAt = useSyncExternalStore(
    (cb) => cache.subscribe(cb),
    () => cache.getAll().filter(stale).reduce((m, q) => (m && m < q.state.dataUpdatedAt ? m : q.state.dataUpdatedAt), 0),
  )
  const failing = savedAt ? cache.getAll().find(stale) : undefined
  if (!savedAt || !failing) return null

  const kind = errorKind(failing.state.error)
  const msg = kind === "offline" ? "Offline" : kind === "private" ? "Not signed in" : "Couldn't refresh"
  return (
    <div role="status" className="mt-3 flex flex-wrap items-center gap-x-3 gap-y-1 rounded-lg border bg-warning px-3 py-2 text-sm text-warning-foreground">
      <CloudOff className="size-4 shrink-0" aria-hidden />
      <span>
        <b className="font-semibold">{msg}</b>, showing data from {when(savedAt)}
      </span>
      <Button
        size="sm" variant="outline" className="ml-auto h-9"
        onClick={() => qc.refetchQueries({ predicate: stale })}
      >
        <RotateCw aria-hidden /> Try again
      </Button>
    </div>
  )
}
