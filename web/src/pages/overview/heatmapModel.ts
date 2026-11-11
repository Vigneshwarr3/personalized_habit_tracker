/** Pure helpers for the hour-by-day heatmap (kept apart from the chart so they can be unit-tested). */
import type { HeatCell } from "@/lib/queries"
import { localTs } from "@/lib/time"

export const UNCLASSIFIED_COLOR = "#C9B6E4" // distinct from all seeded colors; blank cells = no data

/** Categories in view with their colors, in category-id order (color follows the category, not its rank). */
export function presentCategories(cells: HeatCell[]): [string, string][] {
  const seen = new Map<string, string>()
  const sorted = [...cells].sort((a, b) =>
    (a.category_id === null ? 1 : 0) - (b.category_id === null ? 1 : 0) || (a.category_id ?? 0) - (b.category_id ?? 0))
  for (const c of sorted) if (!seen.has(c.category)) seen.set(c.category, c.color ?? UNCLASSIFIED_COLOR)
  return [...seen.entries()]
}

const esc = (s: string) => s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
const hh = (h: number) => String(h).padStart(2, "0")

export function hoverText(date: string, hour: number, c: HeatCell | undefined): string {
  if (!c) return `${date}  ${hh(hour)}:00<br>No data`
  const parts = Object.entries(c.minutes).sort((a, b) => b[1] - a[1])
    .map(([k, v]) => `${esc(k.split(" › ").slice(-1)[0])}: ${v} min`).join("<br>")
  const devs = c.devices?.length ? c.devices.join(", ") : "manual label"
  const where = (c.places ?? []).join(", ")
  return `<b>${esc(c.category)}</b><br>${date}  ${hh(hour)}:00–${hh(hour + 1)}:00<br>${parts}` +
    `<br><i>from: ${esc(devs)}</i>` + (where ? `<br><i>where: ${esc(where)}</i>` : "")
}

/** "date|hour" keys for picked cells. */
export const cellKey = (date: string, hour: number) => `${date}|${hour}`

/** Back-to-back picked hours of the same day become one label: [date, fromHour, toHour). */
export function mergeSpans(keys: Iterable<string>): [string, number, number][] {
  const picked = [...keys].map((k) => { const [d, h] = k.split("|"); return [d, Number(h)] as [string, number] })
    .sort((a, b) => (a[0] < b[0] ? -1 : a[0] > b[0] ? 1 : a[1] - b[1]))
  const spans: [string, number, number][] = []
  for (const [d, h] of picked) {
    const last = spans[spans.length - 1]
    if (last && last[0] === d && last[2] === h) last[2] = h + 1
    else spans.push([d, h, h + 1])
  }
  return spans
}

/** The labels to POST for painting the picked hours. */
export function paintLabels(keys: Iterable<string>, tz: string, category: number) {
  return mergeSpans(keys).map(([d, h0, h1]) => ({
    start_ts: localTs(d, h0, tz), end_ts: localTs(d, h1, tz), category, note: "painted",
  }))
}

/** SVG path of a rounded rectangle (plot coordinates). */
export function rounded(x0: number, y0: number, x1: number, y1: number, rx: number, ry: number): string {
  return `M${x0 + rx},${y0} L${x1 - rx},${y0} Q${x1},${y0} ${x1},${y0 + ry} L${x1},${y1 - ry} Q${x1},${y1} ${x1 - rx},${y1} ` +
    `L${x0 + rx},${y1} Q${x0},${y1} ${x0},${y1 - ry} L${x0},${y0 + ry} Q${x0},${y0} ${x0 + rx},${y0} Z`
}

/** Show every date label on short ranges, fewer on long ones. */
export function tickStep(nDays: number, narrow: boolean): number {
  if (nDays <= (narrow ? 8 : 16)) return 1
  return nDays <= (narrow ? 16 : 31) ? 2 : 7
}
