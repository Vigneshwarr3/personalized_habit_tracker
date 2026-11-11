/** Dates in the tracker's timezone (from /status), independent of the device's own timezone.
 * Dates are "YYYY-MM-DD" strings; timestamps are unix seconds, like the API. */

function parts(ms: number, tz: string) {
  const f = new Intl.DateTimeFormat("en-CA", {
    timeZone: tz, year: "numeric", month: "2-digit", day: "2-digit",
    hour: "2-digit", minute: "2-digit", second: "2-digit", hourCycle: "h23",
  })
  const p: Record<string, number> = {}
  for (const { type, value } of f.formatToParts(ms)) if (type !== "literal") p[type] = Number(value)
  return p as { year: number; month: number; day: number; hour: number; minute: number; second: number }
}

/** How far `tz` is ahead of UTC at instant `ms`, in ms. */
function offsetMs(ms: number, tz: string): number {
  const p = parts(ms, tz)
  const asUtc = Date.UTC(p.year, p.month - 1, p.day, p.hour, p.minute, p.second)
  return asUtc - Math.floor(ms / 1000) * 1000
}

const pad = (n: number) => String(n).padStart(2, "0")
const ymd = (y: number, m: number, d: number) => `${y}-${pad(m)}-${pad(d)}`

export function todayIn(tz: string, nowMs: number = Date.now()): string {
  const p = parts(nowMs, tz)
  return ymd(p.year, p.month, p.day)
}

export function addDays(date: string, n: number): string {
  const [y, m, d] = date.split("-").map(Number)
  const t = new Date(Date.UTC(y, m - 1, d + n))
  return ymd(t.getUTCFullYear(), t.getUTCMonth() + 1, t.getUTCDate())
}

export function daysBetween(a: string, b: string): number {
  const toMs = (s: string) => { const [y, m, d] = s.split("-").map(Number); return Date.UTC(y, m - 1, d) }
  return Math.round((toMs(b) - toMs(a)) / 86_400_000)
}

/** Unix seconds for `date` at `hour`:`minute` in `tz`. hour 24 = midnight at the end of that day. Handles DST. */
export function localTs(date: string, hour: number, tz: string, minute = 0): number {
  const [y, m, d] = date.split("-").map(Number)
  const guess = Date.UTC(y, m - 1, d, hour, minute)
  const off1 = offsetMs(guess, tz)
  let ms = guess - off1
  const off2 = offsetMs(ms, tz)
  if (off2 !== off1) ms = guess - off2
  return ms / 1000
}

/** "14:05" in `tz`. */
export function fmtTime(ts: number, tz: string): string {
  const p = parts(ts * 1000, tz)
  return `${pad(p.hour)}:${pad(p.minute)}`
}

function asUtcDate(date: string): Date {
  const [y, m, d] = date.split("-").map(Number)
  return new Date(Date.UTC(y, m - 1, d))
}

/** Format a "YYYY-MM-DD" date, e.g. fmtDate(d, {weekday: "short"}) -> "Mon". */
export function fmtDate(date: string, opts: Intl.DateTimeFormatOptions): string {
  return new Intl.DateTimeFormat("en-US", { ...opts, timeZone: "UTC" }).format(asUtcDate(date))
}

/** "Nov 05 – Nov 11, 2026" */
export function fmtRange(start: string, end: string): string {
  const md = (s: string) => fmtDate(s, { month: "short", day: "2-digit" })
  return `${md(start)} – ${md(end)}, ${end.slice(0, 4)}`
}

/** Hours like Python's f"{h:g}": 2.5 -> "2.5", 3 -> "3", 1.25 -> "1.25". */
export function fmtHours(h: number): string {
  return String(Math.round(h * 100) / 100)
}

/** "+1.5" / "-0.3" (one decimal, always signed) */
export function fmtSigned(n: number): string {
  const r = Math.round(n * 10) / 10
  return (r >= 0 ? "+" : "") + r.toFixed(1)
}

/** Unix seconds for a "YYYY-MM-DD" date and an "HH:MM" time (from <input type="time">) in `tz`. */
export function tsAt(date: string, hhmm: string, tz: string): number {
  const [h, m] = hhmm.split(":").map(Number)
  return localTs(date, h, tz, m)
}

/** The "YYYY-MM-DD" date of a timestamp in `tz`. */
export function dateOf(ts: number, tz: string): string {
  return todayIn(tz, ts * 1000)
}

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]

/** A timestamp in `tz`, like the old dashboard's fmt_dt:
 * full "2026-11-05 14:03" · short "Nov 05 14:03" · seconds "Nov 05 14:03:09" · date "2026-11-05". Missing -> "never". */
export function fmtTs(ts: number | null | undefined, tz: string, style: "full" | "short" | "seconds" | "date" = "full"): string {
  if (!ts) return "never"
  const p = parts(ts * 1000, tz)
  const date = ymd(p.year, p.month, p.day)
  const hm = `${pad(p.hour)}:${pad(p.minute)}`
  switch (style) {
    case "date": return date
    case "short": return `${MONTHS[p.month - 1]} ${pad(p.day)} ${hm}`
    case "seconds": return `${MONTHS[p.month - 1]} ${pad(p.day)} ${hm}:${pad(p.second)}`
    default: return `${date} ${hm}`
  }
}

/** 0.834 -> "83%", missing -> "—" */
export const pct = (x: number | null | undefined) => (x === null || x === undefined ? "—" : `${Math.round(x * 100)}%`)
