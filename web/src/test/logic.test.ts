import { describe, expect, it } from "vitest"
import { addDays, daysBetween, fmtHours, fmtRange, fmtSigned, fmtTime, localTs, todayIn } from "@/lib/time"
import { rangeFor } from "@/pages/overview/range"
import { cellKey, hoverText, mergeSpans, paintLabels, presentCategories, tickStep, UNCLASSIFIED_COLOR } from "@/pages/overview/heatmapModel"
import { groupByActivity } from "@/pages/overview/activity"
import type { HeatCell, SummaryRow } from "@/lib/queries"

const TZ = "America/Indiana/Indianapolis"

describe("time in the tracker's timezone", () => {
  it("today follows the tracker timezone, not the device", () => {
    const ms = Date.UTC(2026, 10, 11, 3, 30) // 03:30 UTC = 22:30 the day before in Indiana
    expect(todayIn(TZ, ms)).toBe("2026-11-10")
    expect(todayIn("UTC", ms)).toBe("2026-11-11")
  })
  it("local hour -> unix seconds (EST)", () => {
    expect(localTs("2026-11-03", 9, TZ)).toBe(Date.UTC(2026, 10, 3, 14) / 1000)
    expect(fmtTime(localTs("2026-11-03", 9, TZ), TZ)).toBe("09:00")
  })
  it("hour 24 is midnight at the end of the day", () => {
    expect(localTs("2026-11-03", 24, TZ)).toBe(localTs("2026-11-04", 0, TZ))
  })
  it("handles daylight-saving days", () => {
    // 2026-03-08: clocks jump 02:00 -> 03:00 (23-hour day); 2026-11-01: 02:00 -> 01:00 (25-hour day)
    expect(localTs("2026-03-09", 0, TZ) - localTs("2026-03-08", 0, TZ)).toBe(23 * 3600)
    expect(localTs("2026-11-02", 0, TZ) - localTs("2026-11-01", 0, TZ)).toBe(25 * 3600)
    expect(fmtTime(localTs("2026-03-08", 12, TZ), TZ)).toBe("12:00")
  })
  it("date arithmetic across months and years", () => {
    expect(addDays("2026-03-01", -1)).toBe("2026-02-28")
    expect(addDays("2026-12-31", 1)).toBe("2027-01-01")
    expect(daysBetween("2026-11-05", "2026-11-11")).toBe(6)
  })
  it("formats like the old dashboard", () => {
    expect(fmtHours(2.5)).toBe("2.5"); expect(fmtHours(3)).toBe("3"); expect(fmtHours(1.256)).toBe("1.26")
    expect(fmtSigned(1.25)).toBe("+1.3"); expect(fmtSigned(-0.3)).toBe("-0.3"); expect(fmtSigned(0)).toBe("+0.0")
    expect(fmtRange("2026-11-05", "2026-11-11")).toBe("Nov 05 – Nov 11, 2026")
  })
})

describe("overview ranges", () => {
  const today = "2026-11-11"
  it("presets", () => {
    expect(rangeFor(null, today)).toEqual({ key: "last7", start: "2026-11-05", end: today })
    expect(rangeFor("last14", today).start).toBe("2026-10-29")
    expect(rangeFor("last30", today).start).toBe("2026-10-13")
    expect(rangeFor("month", today)).toEqual({ key: "month", start: "2026-11-01", end: today })
    expect(rangeFor("nonsense", today).key).toBe("last7")
  })
  it("preset keys don't contain dates, so yesterday's saved copy shows while refreshing", () => {
    expect(rangeFor("last7", "2026-11-12").key).toBe(rangeFor("last7", today).key)
  })
  it("custom ranges are keyed by their dates, swapped if backwards, and fall back on bad input", () => {
    expect(rangeFor("custom", today, "2026-11-09", "2026-11-01")).toEqual(
      { key: "custom:2026-11-01:2026-11-09", start: "2026-11-01", end: "2026-11-09" })
    expect(rangeFor("custom", today, "junk", null)).toEqual(
      { key: "custom:2026-11-05:2026-11-11", start: "2026-11-05", end: today })
  })
})

const cell = (over: Partial<HeatCell>): HeatCell => ({
  date: "2026-11-03", hour: 9, category_id: 1, category: "Learning › General", color: "#111111",
  minutes: { "Learning › General": 40, "Career › LinkedIn": 20 }, devices: ["mac"], places: [], ...over,
})

describe("heatmap", () => {
  it("categories in id order, unclassified last with its own color", () => {
    const cells = [cell({ category_id: 5, category: "B", color: "#bbbbbb" }), cell({ category_id: null, category: "Unclassified", color: null }),
      cell({ category_id: 2, category: "A", color: "#aaaaaa" }), cell({ category_id: 5, category: "B", color: "#bbbbbb" })]
    expect(presentCategories(cells)).toEqual([["A", "#aaaaaa"], ["B", "#bbbbbb"], ["Unclassified", UNCLASSIFIED_COLOR]])
  })
  it("hover text: biggest first, short names, escaped", () => {
    const t = hoverText("2026-11-03", 9, cell({ category: "<script>", places: ["Home"] }))
    expect(t).toContain("&lt;script&gt;")
    expect(t.indexOf("General: 40 min")).toBeLessThan(t.indexOf("LinkedIn: 20 min"))
    expect(t).toContain("09:00–10:00")
    expect(t).toContain("where: Home")
    expect(hoverText("2026-11-03", 7, undefined)).toBe("2026-11-03  07:00<br>No data")
    expect(hoverText("2026-11-03", 7, cell({ devices: [] }))).toContain("from: manual label")
  })
  it("picked hours merge into back-to-back spans per day", () => {
    const keys = [cellKey("2026-11-04", 23), cellKey("2026-11-03", 10), cellKey("2026-11-03", 9), cellKey("2026-11-03", 12)]
    expect(mergeSpans(keys)).toEqual([["2026-11-03", 9, 11], ["2026-11-03", 12, 13], ["2026-11-04", 23, 24]])
  })
  it("paint labels use the tracker timezone", () => {
    const [a, b] = paintLabels([cellKey("2026-11-03", 9), cellKey("2026-11-04", 23)], TZ, 7)
    expect(a).toEqual({ start_ts: localTs("2026-11-03", 9, TZ), end_ts: localTs("2026-11-03", 10, TZ), category: 7, note: "painted" })
    expect(b.end_ts).toBe(localTs("2026-11-05", 0, TZ))
  })
  it("fewer date labels on long ranges", () => {
    expect(tickStep(7, false)).toBe(1); expect(tickStep(14, true)).toBe(2); expect(tickStep(30, false)).toBe(2)
    expect(tickStep(60, false)).toBe(7)
  })
})

describe("hours per activity", () => {
  const row = (category: string, hours: number, activity: string | null, color = "#123456"): SummaryRow =>
    ({ category_id: 1, category, color, hours, activity_id: 1, activity, activity_color: activity ? "#abcdef" : null })
  it("groups sub-activities under their activity, biggest first", () => {
    const { main, acts } = groupByActivity([row("Learning › A", 1.5, "Learning"), row("Career › X", 3, "Career"),
      row("Learning › B", 2, "Learning"), row("Loose", 0.25, null, "#999999")])
    expect(main).toEqual([
      { name: "Learning", color: "#abcdef", hours: 3.5 }, { name: "Career", color: "#abcdef", hours: 3 },
      { name: "Loose", color: "#999999", hours: 0.25 }])
    expect(acts.get("Learning")!.subs.map((s) => s.name)).toEqual(["A", "B"])
  })
})
