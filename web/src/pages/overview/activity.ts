import type { SummaryRow } from "@/lib/queries"

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
