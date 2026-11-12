/** Hours per activity. Each slice is labelled with its name and share (outside, with a line, when it doesn't fit).
 * Hover a slice (phone: tap) for its total, average per tracked day and top 3 sub-activities; click it (phone: tap it
 * again) to swap the pie for that activity's full breakdown. */
import { useMemo, useState } from "react"
import { Cell, Pie, PieChart, Tooltip, type PieLabelRenderProps } from "recharts"
import { ArrowLeft } from "lucide-react"
import { Button } from "@/components/ui/button"
import { ChartContainer } from "@/components/ui/chart"
import { useIsNarrow, useIsTouch } from "@/lib/device"
import type { SummaryRow } from "@/lib/queries"
import { fmtHours } from "@/lib/time"
import { MIN_LABEL_SHARE, fitsInside, groupByActivity, perDay, textOn, top3, wrapName, type Item } from "./activity"

const RADIAN = Math.PI / 180

/** A slice with what its tooltip shows. */
interface Slice extends Item { avg: number | null; top?: Item[] }

const pctOf = (h: number, total: number) => `${Math.round((100 * h) / (total || 1))}%`

function Details({ s, total }: { s: Slice; total: number }) {
  return (
    <div className="min-w-44 space-y-1 rounded-lg border bg-popover px-3 py-2 text-sm text-popover-foreground shadow-md">
      <p className="flex items-center gap-2 font-semibold">
        <span aria-hidden className="size-2.5 rounded-[3px]" style={{ background: s.color }} />{s.name}
      </p>
      <p className="tabular-nums">{fmtHours(s.hours)} h in total · {pctOf(s.hours, total)}</p>
      <p className="tabular-nums">{s.avg === null ? "—" : `${fmtHours(s.avg)} h`} per day on average</p>
      {s.top && s.top.length > 0 && (
        <div className="pt-1">
          <p className="text-xs text-muted-foreground">Top sub-activities</p>
          <ul>
            {s.top.map((t) => (
              <li key={t.name} className="flex justify-between gap-3 tabular-nums">
                <span>{t.name}</span><span>{fmtHours(t.hours)} h</span>
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  )
}

/** Name + share inside the slice, or outside with a line when it doesn't fit; none for tiny slices. */
function makeLabel(total: number) {
  return function SliceLabel(p: PieLabelRenderProps) {
    const s = p.payload as Slice
    const share = s.hours / (total || 1)
    if (share < MIN_LABEL_SHARE) return null
    const cx = Number(p.cx), cy = Number(p.cy), R = Number(p.outerRadius), a = -Number(p.midAngle) * RADIAN
    const lines = [...wrapName(s.name), pctOf(s.hours, total)]
    const cos = Math.cos(a), sin = Math.sin(a)
    const tspans = (x: number) => lines.map((l, i) => (
      <tspan key={i} x={x} dy={i ? 14 : 0} fontWeight={i === lines.length - 1 ? 600 : 400}>{l}</tspan>
    ))
    const top = (y: number) => y - ((lines.length - 1) * 14) / 2
    if (fitsInside(lines.slice(0, -1), share, R)) {
      const x = cx + R * 0.6 * cos, y = cy + R * 0.6 * sin
      return <text x={x} y={top(y)} textAnchor="middle" dominantBaseline="central" fontSize={12} fill={textOn(s.color)}
        pointerEvents="none">{tspans(x)}</text>
    }
    const [x0, y0] = [cx + (R + 2) * cos, cy + (R + 2) * sin]
    const [x1, y1] = [cx + (R + 14) * cos, cy + (R + 14) * sin]
    const right = cos >= 0, x2 = x1 + (right ? 10 : -10), tx = x2 + (right ? 4 : -4)
    return (
      <g pointerEvents="none">{/* taps go through labels to the slice */}
        <path d={`M${x0},${y0} L${x1},${y1} L${x2},${y1}`} stroke={s.color} strokeWidth={1.5} fill="none" />
        <text x={tx} y={top(y1)} textAnchor={right ? "start" : "end"} dominantBaseline="central" fontSize={12}
          className="fill-foreground">{tspans(tx)}</text>
      </g>
    )
  }
}

function PieView({ slices, title, onPick, showLegend }: {
  slices: Slice[]; title: string; onPick?: (name: string) => void; showLegend: boolean
}) {
  const touch = useIsTouch(), narrow = useIsNarrow()
  const [armed, setArmed] = useState<string | null>(null) // phone: the slice tapped once (its details show below)
  const total = slices.reduce((t, s) => t + s.hours, 0)
  const R = narrow ? 78 : 100
  const label = useMemo(() => makeLabel(total), [total])

  const tap = (name: string) => {
    if (!touch) return onPick?.(name)
    if (armed === name && onPick) { setArmed(null); onPick(name) } else setArmed(name)
  }
  const armedSlice = touch ? slices.find((s) => s.name === armed) : undefined

  return (
    <div>
      <ChartContainer config={{}} role="img" aria-label={title}
        className="aspect-auto h-[260px] w-full sm:h-[320px] [&_.recharts-surface]:overflow-visible">
        <PieChart margin={{ top: 16, bottom: 16, left: 8, right: 8 }}>
          {!touch && (
            <Tooltip cursor={false} isAnimationActive={false}
              content={({ active, payload }) => (active && payload?.[0] ? <Details s={payload[0].payload as Slice} total={total} /> : null)} />
          )}
          <Pie data={slices} dataKey="hours" nameKey="name" outerRadius={R} startAngle={90} endAngle={-270}
            stroke="var(--background)" strokeWidth={2} labelLine={false} label={label} isAnimationActive={false}
            onClick={(d) => tap((d as unknown as Slice).name)} className={onPick || touch ? "cursor-pointer" : undefined}>
            {slices.map((s) => (
              <Cell key={s.name} fill={s.color} opacity={touch && armed && armed !== s.name ? 0.5 : 1} />
            ))}
          </Pie>
        </PieChart>
      </ChartContainer>
      {armedSlice && (
        <div className="mt-1 space-y-1" aria-live="polite">
          <Details s={armedSlice} total={total} />
          {onPick && <p className="text-xs text-muted-foreground">Tap {armedSlice.name} again to see its breakdown.</p>}
        </div>
      )}
      {showLegend && (
        <ul className="mt-2 space-y-1 text-sm">
          {slices.map((s) => {
            const text = `${fmtHours(s.hours)} Hours of ${s.name}, ${pctOf(s.hours, total)}`
            const swatch = <span aria-hidden className="size-[11px] flex-none rounded-[3px]" style={{ background: s.color }} />
            return (
              <li key={s.name}>
                {onPick ? (
                  <button type="button" onClick={() => onPick(s.name)}
                    className="flex min-h-8 w-full items-center gap-2 rounded-md px-1 text-left hover:bg-accent focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none">
                    {swatch}{text}
                  </button>
                ) : <span className="flex min-h-7 items-center gap-2 px-1">{swatch}{text}</span>}
              </li>
            )
          })}
        </ul>
      )}
    </div>
  )
}

export function ActivityPie({ rows, days, showLegend }: { rows: SummaryRow[]; days: number; showLegend: boolean }) {
  const [pick, setPick] = useState<string | null>(null)
  const touch = useIsTouch()
  const { acts, main } = useMemo(() => groupByActivity(rows), [rows])
  if (!rows.length) return <p className="rounded-lg border bg-muted px-4 py-3 text-sm">No classified time in this range yet.</p>

  const sel = pick && acts.has(pick) ? pick : null
  if (sel) {
    const a = acts.get(sel)!
    const subs: Slice[] = [...a.subs].sort((x, y) => y.hours - x.hours).map((s) => ({ ...s, avg: perDay(s.hours, days) }))
    return (
      <div className="space-y-2">
        <div className="flex flex-wrap items-center gap-2">
          <Button variant="outline" className="h-9" onClick={() => setPick(null)}><ArrowLeft aria-hidden /> All activities</Button>
          <p className="font-semibold">{sel} · {fmtHours(a.hours)} h</p>
        </div>
        <PieView key={sel} slices={subs} showLegend={showLegend} title={`Pie chart of ${sel} by sub-activity.`} />
      </div>
    )
  }
  const slices: Slice[] = main.map((m) => ({ ...m, avg: perDay(m.hours, days), top: top3(acts.get(m.name)!.subs) }))
  return (
    <div className="space-y-2">
      <PieView slices={slices} onPick={setPick} showLegend={showLegend} title="Pie chart of hours per activity." />
      <p className="text-sm text-muted-foreground">
        {touch ? "Tap a slice for its details, tap it again for its breakdown." : "Hover a slice for its details; click it for its breakdown."}
      </p>
    </div>
  )
}
