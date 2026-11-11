/** Hour-by-day heatmap with painting: pick hours (drag a box, or click/tap hours), then choose a category. */
import { useMemo, useRef, useState } from "react"
import { useMutation, useQueryClient } from "@tanstack/react-query"
import type { Data, Layout, Shape } from "plotly.js"
import { Paintbrush, Undo2, X } from "lucide-react"
import { Plot } from "@/components/Plot"
import { Button } from "@/components/ui/button"
import { Card } from "@/components/ui/card"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { api } from "@/lib/api"
import { useIsDark, useIsNarrow, useIsTouch } from "@/lib/device"
import { useCategories, type Dashboard, type HeatCell } from "@/lib/queries"
import { fmtDate } from "@/lib/time"
import {
  UNCLASSIFIED_COLOR, cellKey, hoverText, paintLabels, presentCategories, rounded, tickStep,
} from "./heatmapModel"

const MAX_ROUNDED_DAYS = 45 // beyond this, plain squares (hundreds of rounded shapes get slow)
const HOURS = Array.from({ length: 24 }, (_, i) => i)

export function Heatmap({ data, today, tz }: { data: Dashboard; today: string; tz: string }) {
  const narrow = useIsNarrow()
  const touch = useIsTouch()
  const dark = useIsDark()
  const [picked, setPicked] = useState<Set<string>>(new Set())
  const { dates, heatmap: cells } = data
  const present = useMemo(() => presentCategories(cells), [cells])

  const { traces, layout } = useMemo(() => {
    const EMPTY = dark ? "rgba(255,255,255,0.05)" : "rgba(20,24,30,0.045)"
    const TODAY_BG = dark ? "rgba(255,255,255,0.07)" : "rgba(20,24,30,0.06)"
    const SELECTED = dark ? "rgba(255,255,255,0.45)" : "rgba(20,24,30,0.35)"
    const INK = dark ? "#e6e8eb" : "#1d2127"
    const n = dates.length
    const col = new Map(dates.map((d, i) => [d, i]))
    const byCell = new Map<string, HeatCell>()
    for (const c of cells) if (col.has(c.date)) byCell.set(cellKey(c.date, c.hour), c)

    const traces: Data[] = []
    const shapes: Partial<Shape>[] = []
    if (n <= MAX_ROUNDED_DAYS) {
      const t = col.get(today)
      if (t !== undefined) shapes.push({ type: "path", path: rounded(t - 0.5, -0.6, t + 0.5, 23.6, 0.15, 0.4),
        fillcolor: TODAY_BG, line: { width: 0 }, layer: "below" })
      for (const h of HOURS) for (let j = 0; j < n; j++) {
        const key = cellKey(dates[j], h)
        const c = byCell.get(key)
        shapes.push({ type: "path", path: rounded(j - 0.42, h - 0.38, j + 0.42, h + 0.38, 0.1, 0.18),
          fillcolor: c ? (c.color ?? UNCLASSIFIED_COLOR) : EMPTY, layer: "below",
          line: picked.has(key) ? { width: 2.5, color: INK } : { width: 0 } })
      }
    } else { // long ranges: plain colored squares
      const names = present.map(([name]) => name)
      const k = Math.max(1, names.length)
      const scale: [number, string][] = present.flatMap(([, color], i) => [[i / k, color], [(i + 1) / k, color]] as [number, string][])
      traces.push({
        type: "heatmap", x: dates.map((_, j) => j), y: HOURS, zmin: 0, zmax: k, showscale: false, xgap: 2, ygap: 2,
        hoverinfo: "skip", colorscale: scale.length ? scale : undefined,
        z: HOURS.map((h) => dates.map((d) => {
          const c = byCell.get(cellKey(d, h))
          return c ? names.indexOf(c.category) + 0.5 : null
        })),
      } as Data)
    }

    // an invisible point on every hour: hover/tap details + picking hours to paint
    const xs: number[] = [], ys: number[] = [], keys: string[] = [], hover: string[] = [], colors: string[] = []
    for (const h of HOURS) for (let j = 0; j < n; j++) {
      const key = cellKey(dates[j], h)
      xs.push(j); ys.push(h); keys.push(key)
      hover.push(hoverText(dates[j], h, byCell.get(key)))
      // picked hours: outlined tile (short ranges) or a filled marker (long ranges, no tiles)
      colors.push(picked.has(key) && n > MAX_ROUNDED_DAYS ? SELECTED : "rgba(0,0,0,0)")
    }
    traces.push({
      type: "scatter", mode: "markers", x: xs, y: ys, customdata: keys, hovertext: hover,
      hovertemplate: "%{hovertext}<extra></extra>",
      marker: { symbol: "square", size: narrow ? 12 : 18, color: colors },
      selected: { marker: { opacity: 1 } }, unselected: { marker: { opacity: 1 } },
    } as Data)

    const step = tickStep(n, narrow)
    const ticks = dates.map((_, j) => j).filter((j) => j % step === 0)
    const labels = ticks.map((j) => {
      const lab = n <= 16 ? `${fmtDate(dates[j], { weekday: "short" })}<br>${dates[j].slice(8)}` : dates[j].slice(8)
      return dates[j] === today ? `<b>${lab}</b>` : lab
    })
    const layout = {
      margin: { l: 4, r: 4, t: 4, b: 4 },
      font: { family: "Inter Variable, system-ui, sans-serif", color: INK, size: 12 },
      xaxis: { side: "top", tickvals: ticks, ticktext: labels, range: [-0.55, n - 0.45], showgrid: false,
        zeroline: false, fixedrange: true, automargin: true },
      yaxis: { tickvals: HOURS, ticktext: HOURS.map((h) => `${String(h).padStart(2, "0")}:00`),
        range: [23.6, -0.6], showgrid: false, zeroline: false, fixedrange: true, automargin: true },
      shapes, selections: [],
      plot_bgcolor: "rgba(0,0,0,0)", paper_bgcolor: "rgba(0,0,0,0)", showlegend: false,
      hoverlabel: { bgcolor: dark ? "#1c1f24" : "#ffffff", bordercolor: dark ? "#2b2f36" : "#e2e5e9",
        font: { color: INK, family: "Inter Variable, system-ui, sans-serif" } },
      dragmode: touch ? false : "select", clickmode: "event", hovermode: "closest",
    } as Partial<Layout> // `selections: []` clears the box you dragged (not in the type definitions yet)
    return { traces, layout }
  }, [cells, dates, present, picked, today, dark, narrow, touch])

  // On touch screens Plotly reports one tap twice (the touch, then the browser's emulated mouse click):
  // ignore a repeat on the same hour within half a second, or the tap would pick and un-pick it.
  const lastClick = useRef({ key: "", at: 0 })
  const onTap = (key: string) => {
    const now = Date.now()
    if (lastClick.current.key === key && now - lastClick.current.at < 500) return
    lastClick.current = { key, at: now }
    toggle(key)
  }
  const toggle = (key: string) => setPicked((s) => {
    const next = new Set(s)
    if (next.has(key)) next.delete(key)
    else next.add(key)
    return next
  })

  return (
    <div>
      <Plot
        data={traces} layout={layout} height={narrow ? 560 : 600}
        ariaLabel={`Heatmap of your hours by day from ${dates[0]} to ${dates[dates.length - 1]}; each hour is colored by its category.`}
        onClick={(e) => { const k = e.points[0]?.customdata; if (typeof k === "string") onTap(k) }}
        onSelected={(e) => {
          const keys = (e?.points ?? []).map((p) => p.customdata).filter((k): k is string => typeof k === "string")
          if (keys.length) setPicked((s) => new Set([...s, ...keys]))
        }}
      />
      <PaintPanel picked={picked} tz={tz} onDone={() => setPicked(new Set())} touch={touch} />
      {present.length > 0 && (
        <ul className="mt-3 flex flex-wrap gap-x-3 gap-y-1 text-sm" aria-label="Categories in view">
          {present.map(([name, color]) => (
            <li key={name} className="inline-flex items-center gap-1.5">
              <span aria-hidden className="size-3 rounded-[4px]" style={{ background: color }} />{name}
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}

function PaintPanel({ picked, tz, onDone, touch }: { picked: Set<string>; tz: string; onDone: () => void; touch: boolean }) {
  const qc = useQueryClient()
  const cats = useCategories(false)
  const [cat, setCat] = useState<string>("")
  const [done, setDone] = useState<{ ids: number[]; text: string } | null>(null)
  const refresh = () => Promise.all([
    qc.invalidateQueries({ queryKey: ["dashboard"] }), qc.invalidateQueries({ queryKey: ["kpis"] }),
  ])

  const paint = useMutation({
    mutationFn: async () => {
      const ids: number[] = []
      for (const label of paintLabels(picked, tz, Number(cat))) ids.push((await api.post<{ id: number }>("/labels", label)).id)
      return ids
    },
    onSuccess: async (ids) => {
      const name = cats.data?.find((c) => String(c.id) === cat)?.name ?? "category"
      setDone({ ids, text: `Painted ${picked.size} hour${picked.size > 1 ? "s" : ""} as ${name}.` })
      onDone()
      await refresh()
    },
  })
  const undo = useMutation({
    mutationFn: async (ids: number[]) => { for (const id of ids) await api.post(`/labels/${id}/undo`) },
    onSuccess: async () => { setDone(null); await refresh() },
  })
  const failure = paint.error ?? undo.error

  if (picked.size === 0) {
    return (
      <div className="mt-2 min-h-6 text-sm text-muted-foreground" role="status" aria-live="polite">
        {done ? (
          <span className="inline-flex flex-wrap items-center gap-2 text-foreground">
            {done.text}
            <Button size="sm" variant="outline" className="h-8" disabled={undo.isPending} onClick={() => undo.mutate(done.ids)}>
              <Undo2 aria-hidden /> Undo
            </Button>
          </span>
        ) : touch ? "Tap hours to pick them, then paint them with a category."
          : "Paint: drag a box over hours (or click them), then choose a category."}
        {failure && <p role="alert" className="mt-1 text-destructive">Couldn't save: {failure.message}</p>}
      </div>
    )
  }
  return (
    <Card size="sm" className="mt-2 gap-2 px-4">
      <p className="font-semibold">Paint {picked.size} selected hour{picked.size > 1 ? "s" : ""} as:</p>
      <div className="flex flex-wrap items-center gap-2">
        <Select value={cat} onValueChange={setCat}>
          <SelectTrigger className="h-10 min-w-56 flex-1" aria-label="Category to paint with">
            <SelectValue placeholder={cats.isPending ? "Loading categories…" : "Choose a category"} />
          </SelectTrigger>
          <SelectContent>
            {cats.data?.map((c) => (
              <SelectItem key={c.id} value={String(c.id)}>
                <span aria-hidden className="size-2.5 rounded-[3px]" style={{ background: c.color }} />{c.name}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        <Button className="h-10" disabled={!cat || paint.isPending} onClick={() => paint.mutate()}>
          <Paintbrush aria-hidden /> {paint.isPending ? "Painting…" : "Paint"}
        </Button>
        <Button variant="outline" className="h-10" onClick={onDone} disabled={paint.isPending}>
          <X aria-hidden /> Clear
        </Button>
      </div>
      {failure && <p role="alert" className="text-sm text-destructive">Couldn't save: {failure.message}</p>}
    </Card>
  )
}
