import { addDays } from "@/lib/time"
import type { Range } from "@/lib/queries"

export const PRESETS = [
  { key: "last7", label: "Last 7 days" },
  { key: "last14", label: "Last 14 days" },
  { key: "last30", label: "Last 30 days" },
  { key: "month", label: "This month" },
  { key: "custom", label: "Custom" },
] as const
export type PresetKey = (typeof PRESETS)[number]["key"]

const isDate = (s: string | null): s is string => !!s && /^\d{4}-\d{2}-\d{2}$/.test(s)

/** The range for a preset (or custom dates) given today's date in the tracker's timezone. */
export function rangeFor(preset: string | null, today: string, start?: string | null, end?: string | null): Range {
  switch (preset) {
    case "last14": return { key: "last14", start: addDays(today, -13), end: today }
    case "last30": return { key: "last30", start: addDays(today, -29), end: today }
    case "month": return { key: "month", start: `${today.slice(0, 8)}01`, end: today }
    case "custom": {
      let a = isDate(start ?? null) ? start! : addDays(today, -6)
      let b = isDate(end ?? null) ? end! : today
      if (a > b) [a, b] = [b, a]
      return { key: `custom:${a}:${b}`, start: a, end: b }
    }
    default: return { key: "last7", start: addDays(today, -6), end: today }
  }
}
