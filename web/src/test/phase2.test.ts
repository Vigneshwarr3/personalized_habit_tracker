import { describe, expect, it } from "vitest"
import { fmtTs, localTs, tsAt, dateOf } from "@/lib/time"
import { dayRows, howText, mergeBlocks, whatText } from "@/pages/corrections/dayRows"
import type { Window } from "@/lib/queries"
import { pageFor } from "@/pages"

const TZ = "America/Indiana/Indianapolis"
const w = (start: number, over: Partial<Window> = {}): Window => ({
  window_start: start, window_end: start + 900, category_id: 3, category: "Learning › General", color: "#999999",
  source: "routine", routine: "study", confidence: null,
  features: { devices: ["mac"], top_items: [
    { app: "com.google.Chrome", app_name: "Chrome", domain: "youtube.com", title: "A very long lecture title that goes on and on and on past sixty characters", signal: "learning", minutes: 9.5 },
    { app: "com.microsoft.VSCode", app_name: "VS Code", domain: null, title: null, signal: "coding", minutes: 4 },
  ] }, ...over,
})

describe("time helpers (phase 2)", () => {
  it("HH:MM inputs -> timestamps in the tracker timezone", () => {
    expect(tsAt("2026-11-03", "09:15", TZ)).toBe(localTs("2026-11-03", 9, TZ) + 900)
    expect(dateOf(localTs("2026-11-03", 23, TZ), TZ)).toBe("2026-11-03")
  })
  it("formats like the old dashboard", () => {
    const t = localTs("2026-11-05", 14, TZ, 3) + 9
    expect(fmtTs(t, TZ)).toBe("2026-11-05 14:03")
    expect(fmtTs(t, TZ, "short")).toBe("Nov 05 14:03")
    expect(fmtTs(t, TZ, "seconds")).toBe("Nov 05 14:03:09")
    expect(fmtTs(t, TZ, "date")).toBe("2026-11-05")
    expect(fmtTs(null, TZ)).toBe("never")
  })
})

describe("corrections day table", () => {
  it("96 blocks on a normal day, 92/100 on clock-change days", () => {
    expect(dayRows("2026-11-03", TZ, [], {})).toHaveLength(96)
    expect(dayRows("2026-03-08", TZ, [], {})).toHaveLength(92)
    expect(dayRows("2026-11-01", TZ, [], {})).toHaveLength(100)
  })
  it("fills tracked blocks and leaves the rest empty", () => {
    const t9 = localTs("2026-11-03", 9, TZ)
    const rows = dayRows("2026-11-03", TZ, [w(t9)], { [String(t9)]: { label: "Home", state: "home" } })
    const r = rows[36]
    expect(r).toMatchObject({ time: "09:00–09:15", category: "Learning › General", how: "routine: study", devices: "mac", location: "Home", empty: false })
    expect(r.what).toBe("A very long lecture title that goes on and on and on past si [learning] 9.5m; VS Code [coding] 4m")
    expect(rows[35]).toMatchObject({ how: "empty", category: "", what: "", empty: true })
  })
  it("how each block was decided", () => {
    expect(howText(w(0, { source: "label" }))).toBe("your label")
    expect(howText(w(0, { source: "model", confidence: 0.834 }))).toBe("learned model (83%)")
    expect(howText(w(0, { source: "session", routine: null }))).toBe("session")
    expect(howText(w(0, { source: "unclassified" }))).toBe("no routine matched")
    expect(whatText(w(0, { features: null }))).toBe("")
  })
  it("back-to-back blocks merge into one label, in time order", () => {
    expect(mergeBlocks([[1800, 2700], [0, 900], [900, 1800], [3600, 4500]])).toEqual([[0, 2700], [3600, 4500]])
  })
})

describe("header page title", () => {
  it("router paths (no /ui prefix) map to their page", () => {
    expect(pageFor("/")?.title).toBe("Overview")
    expect(pageFor("/settings")?.title).toBe("Settings")
    expect(pageFor("/knowledge-graph/x")?.title).toBe("Knowledge graph")
    expect(pageFor("/nope")).toBeUndefined()
  })
})
