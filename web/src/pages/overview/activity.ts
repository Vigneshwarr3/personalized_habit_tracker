import type { HeatCell, SummaryRow } from "@/lib/queries"

export type Item = { name: string; color: string; hours: number }

/** Group sub-activity rows by their activity, biggest first. */
export function groupByActivity(rows: SummaryRow[]) {
  const acts = new Map<string, { color: string; hours: number; subs: Item[] }>()
  for (const r of rows) {
    const key = r.activity ?? r.category
    const a = acts.get(key) ?? { color: r.activity_color ?? r.color, hours: 0, subs: [] }
    a.hours += r.hours
    a.subs.push({ name: r.category.split(" › ").slice(-1)[0], color: r.color, hours: r.hours })
    acts.set(key, a)
  }
  const main: Item[] = [...acts.entries()].sort((a, b) => b[1].hours - a[1].hours)
    .map(([name, a]) => ({ name, color: a.color, hours: Math.round(a.hours * 100) / 100 }))
  return { acts, main }
}

/** Days in the selected range that have any tracked time (the heatmap only has cells for hours with data). */
export const daysTracked = (cells: HeatCell[]) => new Set(cells.map((c) => c.date)).size

/** Average hours per tracked day (null when nothing was tracked). */
export const perDay = (hours: number, days: number) => (days > 0 ? hours / days : null)

/** The biggest three sub-activities. */
export const top3 = (subs: Item[]) => [...subs].sort((a, b) => b.hours - a.hours).slice(0, 3)

/** Slices smaller than this get no label (their details are still in the tooltip). */
export const MIN_LABEL_SHARE = 0.03

/** Split a name into lines of at most `max` characters, at spaces ("Everyday activities" -> 2 lines). */
export function wrapName(name: string, max = 12): string[] {
  const lines: string[] = []
  for (const word of name.split(" ")) {
    const last = lines[lines.length - 1]
    if (last !== undefined && (last + " " + word).length <= max) lines[lines.length - 1] = last + " " + word
    else lines.push(word)
  }
  return lines
}

const CHAR_W = 6.6, LINE_H = 14 // 12 px Inter, roughly

/** Does a label (name lines + percent line) fit inside its slice? Measured along the arc where it's drawn. */
export function fitsInside(lines: string[], share: number, outerRadius: number): boolean {
  if (share >= 0.5) return true
  const width = Math.max(...lines.map((l) => l.length), 4) * CHAR_W
  const height = (lines.length + 1) * LINE_H
  const r = outerRadius * 0.6
  const arc = 2 * Math.PI * r * share
  return arc >= width * 1.05 && height <= outerRadius * 0.8
}

/** Dark or light text, whichever reads better on the slice color. */
export function textOn(hex: string): string {
  const m = /^#?([0-9a-f]{6})$/i.exec(hex)
  if (!m) return "#111111"
  const n = parseInt(m[1], 16)
  const [r, g, b] = [n >> 16, (n >> 8) & 255, n & 255].map((c) => {
    const s = c / 255
    return s <= 0.03928 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4
  })
  const lum = 0.2126 * r + 0.7152 * g + 0.0722 * b
  return lum > 0.179 ? "#111111" : "#ffffff" // the point where black and white text have equal contrast
}
