/** The whole day as 15-minute blocks (empty ones included), like the old Corrections table. */
import type { LocationState, Window } from "@/lib/queries"
import { addDays, fmtTime, localTs } from "@/lib/time"

export const BLOCK_S = 900

export interface DayRow {
  start: number; end: number; time: string; category: string; categoryId: number | null; color: string | null
  location: string; how: string; devices: string; what: string; empty: boolean
}

export function howText(w: Window): string {
  switch (w.source) {
    case "label": return "your label"
    case "routine": return `routine: ${w.routine}`
    case "location": return "your location"
    case "session": return w.routine || "session"
    case "model": return w.confidence ? `learned model (${Math.round(w.confidence * 100)}%)` : "learned model"
    case "unclassified": return "no routine matched"
    default: return w.source
  }
}

export function whatText(w: Window): string {
  return (w.features?.top_items ?? []).slice(0, 3).map((i) => {
    const name = i.title || i.domain ? (i.title || i.domain)!.slice(0, 60) : (i.app_name || i.app)
    return `${name} [${i.signal}] ${i.minutes}m`
  }).join("; ")
}

export function dayRows(day: string, tz: string, windows: Window[], locations: Record<string, LocationState>): DayRow[] {
  const byStart = new Map(windows.map((w) => [Math.round(w.window_start), w]))
  const a = localTs(day, 0, tz), b = localTs(addDays(day, 1), 0, tz) // 92 or 100 blocks on clock-change days
  const rows: DayRow[] = []
  for (let t = a; t < b; t += BLOCK_S) {
    const w = byStart.get(Math.round(t))
    rows.push({
      start: t, end: t + BLOCK_S, time: `${fmtTime(t, tz)}–${fmtTime(t + BLOCK_S, tz)}`,
      category: w ? (w.category ?? "Unclassified") : "", categoryId: w?.category_id ?? null, color: w?.color ?? null,
      location: locations[String(Math.round(t))]?.label ?? "",
      how: w ? howText(w) : "empty",
      devices: (w?.features?.devices ?? []).join(", "),
      what: w ? (whatText(w) || "(labeled by you)") : "",
      empty: !w,
    })
  }
  return rows
}

/** Back-to-back blocks become one label: [[start, end], ...]. */
export function mergeBlocks(blocks: [number, number][]): [number, number][] {
  const spans: [number, number][] = []
  for (const [a, b] of [...blocks].sort((x, y) => x[0] - y[0])) {
    const last = spans[spans.length - 1]
    if (last && Math.abs(last[1] - a) < 1) last[1] = b
    else spans.push([a, b])
  }
  return spans
}
