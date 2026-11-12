/** Overview: summary cards, hour-by-day heatmap (rows = hours, columns = dates) and hours per activity. */
import { useSearchParams } from "react-router"
import { PauseCircle } from "lucide-react"
import { Label } from "@/components/ui/label"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { Skeleton } from "@/components/ui/skeleton"
import { useDashboard, useKpis, useStatus, useUiSettings } from "@/lib/queries"
import { fmtRange, todayIn } from "@/lib/time"
import { PRESETS, rangeFor } from "./range"
import { Cards } from "./Cards"
import { Heatmap } from "./Heatmap"
import { ActivityPie } from "./ActivityPie"
import { daysTracked } from "./activity"

export function Overview() {
  const [params, setParams] = useSearchParams()
  const status = useStatus()
  const tz = status.data?.timezone
  const today = tz ? todayIn(tz) : undefined
  const preset = params.get("range") ?? "last7"
  const range = today ? rangeFor(preset, today, params.get("start"), params.get("end")) : undefined
  const dash = useDashboard(range)
  const kpis = useKpis(range)
  const showLegends = useUiSettings().data?.show_legends ?? false

  const set = (next: Record<string, string>) => setParams(next, { replace: true })

  return (
    <div className="space-y-5">
      <h1 className="sr-only">Overview</h1>
      <div className="flex flex-wrap items-end gap-x-4 gap-y-2">
        <div className="grid gap-1.5">
          <Label htmlFor="range">Range</Label>
          <Select
            value={preset}
            onValueChange={(v) => set(v === "custom" && range ? { range: v, start: range.start, end: range.end } : { range: v })}
          >
            <SelectTrigger id="range" className="h-10 w-44"><SelectValue /></SelectTrigger>
            <SelectContent>
              {PRESETS.map((p) => <SelectItem key={p.key} value={p.key}>{p.label}</SelectItem>)}
            </SelectContent>
          </Select>
        </div>
        {preset === "custom" && range ? (
          <div className="flex items-end gap-2">
            <div className="grid gap-1.5">
              <Label htmlFor="start">From</Label>
              <input
                id="start" type="date" value={range.start} max={today}
                onChange={(e) => e.target.value && set({ range: "custom", start: e.target.value, end: range.end })}
                className="h-10 rounded-md border border-input bg-background px-3 text-sm"
              />
            </div>
            <div className="grid gap-1.5">
              <Label htmlFor="end">To</Label>
              <input
                id="end" type="date" value={range.end} max={today}
                onChange={(e) => e.target.value && set({ range: "custom", start: range.start, end: e.target.value })}
                className="h-10 rounded-md border border-input bg-background px-3 text-sm"
              />
            </div>
          </div>
        ) : (
          range && <p className="pb-2.5 text-sm text-muted-foreground">{fmtRange(range.start, range.end)}</p>
        )}
        {status.data?.paused && (
          <p className="flex h-10 items-center gap-2 rounded-md bg-warning px-3 text-sm text-warning-foreground">
            <PauseCircle className="size-4" aria-hidden /> Tracking is paused
          </p>
        )}
      </div>

      {kpis.data ? <Cards k={kpis.data} tz={tz!} /> : <Skeleton className="h-28 w-full" />}

      <div className="grid gap-8 lg:grid-cols-[3fr_2fr]">
        <section aria-labelledby="hm-title">
          <h2 id="hm-title" className="mb-2 text-lg font-semibold">Hour by day</h2>
          {dash.data && today ? <Heatmap data={dash.data} today={today} tz={tz!} showLegend={showLegends} /> : <Skeleton className="h-[600px] w-full" />}
        </section>
        <section aria-labelledby="pie-title">
          <h2 id="pie-title" className="mb-2 text-lg font-semibold">Hours per activity</h2>
          {dash.data ? <ActivityPie rows={dash.data.summary.categories} days={daysTracked(dash.data.heatmap)} showLegend={showLegends} /> : <Skeleton className="h-80 w-full" />}
        </section>
      </div>
    </div>
  )
}
