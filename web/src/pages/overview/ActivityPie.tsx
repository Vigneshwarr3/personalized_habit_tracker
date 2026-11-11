/** Hours per activity: tap a slice to see its sub-activities, tap it again to go back. */
import { useMemo, useState } from "react"
import type { Data, Layout } from "plotly.js"
import { Plot } from "@/components/Plot"
import { useIsDark } from "@/lib/device"
import type { SummaryRow } from "@/lib/queries"
import { fmtHours } from "@/lib/time"
import { groupByActivity, type Item } from "./activity"

const label = (it: Item, total: number) => `${fmtHours(it.hours)} Hours of ${it.name}, ${Math.round((100 * it.hours) / (total || 1))}%`

function withAlpha(hex: string, a: number) {
  const m = /^#([0-9a-f]{6})$/i.exec(hex)
  if (!m) return hex
  const n = parseInt(m[1], 16)
  return `rgba(${n >> 16},${(n >> 8) & 255},${n & 255},${a})`
}

function Pie({ items, picked, onPick, title }: { items: Item[]; picked?: string | null; onPick?: (n: string) => void; title: string }) {
  const dark = useIsDark()
  const total = items.reduce((s, i) => s + i.hours, 0)
  const { data, layout } = useMemo(() => {
    const data: Data[] = [{
      type: "pie", sort: false, direction: "clockwise", textinfo: "none",
      labels: items.map((i) => i.name), values: items.map((i) => i.hours),
      hovertext: items.map((i) => label(i, total)), hovertemplate: "%{hovertext}<extra></extra>",
      marker: { colors: items.map((i) => (picked && i.name !== picked ? withAlpha(i.color, 0.45) : i.color)),
        line: { color: dark ? "#121417" : "#ffffff", width: 2 } },
    } as Data]
    const layout: Partial<Layout> = {
      margin: { l: 8, r: 8, t: 8, b: 8 }, showlegend: false,
      paper_bgcolor: "rgba(0,0,0,0)", plot_bgcolor: "rgba(0,0,0,0)",
      hoverlabel: { bgcolor: dark ? "#1c1f24" : "#ffffff", font: { color: dark ? "#e6e8eb" : "#1d2127" } },
    }
    return { data, layout }
  }, [items, picked, dark, total])

  return (
    <div>
      <Plot data={data} layout={layout} height={260} ariaLabel={title}
        onClick={onPick ? (e) => { const l = (e.points[0] as unknown as { label?: unknown })?.label; if (typeof l === "string") onPick(l) } : undefined} />
      <ul className="mt-1 space-y-1 text-sm">
        {items.map((i) => (
          <li key={i.name}>
            {onPick ? (
              <button
                type="button" onClick={() => onPick(i.name)} aria-pressed={picked === i.name}
                className="flex min-h-8 w-full items-center gap-2 rounded-md px-1 text-left hover:bg-accent focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none aria-pressed:font-semibold"
              >
                <span aria-hidden className="size-[11px] flex-none rounded-[3px]" style={{ background: i.color }} />
                {label(i, total)}
              </button>
            ) : (
              <span className="flex min-h-7 items-center gap-2 px-1">
                <span aria-hidden className="size-[11px] flex-none rounded-[3px]" style={{ background: i.color }} />
                {label(i, total)}
              </span>
            )}
          </li>
        ))}
      </ul>
    </div>
  )
}

export function ActivityPie({ rows }: { rows: SummaryRow[] }) {
  const [pick, setPick] = useState<string | null>(null)
  const { acts, main } = useMemo(() => groupByActivity(rows), [rows])
  if (!rows.length) return <p className="rounded-lg border bg-muted px-4 py-3 text-sm">No classified time in this range yet.</p>

  const sel = pick && acts.has(pick) ? pick : null
  const subs = sel ? [...acts.get(sel)!.subs].sort((a, b) => b.hours - a.hours) : []
  return (
    <div className="space-y-4">
      <Pie items={main} picked={sel} onPick={(n) => setPick(n === sel ? null : n)}
        title="Pie chart of hours per activity; the list below has the same numbers." />
      {sel ? (
        <div>
          <p className="font-semibold">{sel} · {fmtHours(acts.get(sel)!.hours)} h</p>
          <Pie items={subs} title={`Pie chart of ${sel} by sub-activity; the list below has the same numbers.`} />
        </div>
      ) : (
        <p className="text-sm text-muted-foreground">Tap a slice (or a line above) to see its sub-activities. Tap it again to go back.</p>
      )}
    </div>
  )
}
