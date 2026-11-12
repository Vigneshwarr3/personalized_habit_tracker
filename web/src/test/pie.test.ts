import { describe, expect, it } from "vitest"
import { daysTracked, fitsInside, MIN_LABEL_SHARE, perDay, textOn, top3, wrapName } from "@/pages/overview/activity"
import type { HeatCell } from "@/lib/queries"

const cell = (date: string, hour: number): HeatCell =>
  ({ date, hour, category_id: 1, category: "x", color: "#000000", minutes: {}, devices: [], places: [] })

describe("pie details", () => {
  it("average per day divides by the days in the range that have tracked data", () => {
    // a 7-day range where only 3 days have data -> divide by 3
    const days = daysTracked([cell("2026-11-09", 9), cell("2026-11-09", 10), cell("2026-11-10", 9), cell("2026-11-11", 20)])
    expect(days).toBe(3)
    expect(perDay(6, days)).toBe(2)
    expect(perDay(5, 0)).toBeNull()
  })
  it("top 3 sub-activities, biggest first", () => {
    const subs = [{ name: "a", color: "", hours: 1 }, { name: "b", color: "", hours: 4 }, { name: "c", color: "", hours: 2 }, { name: "d", color: "", hours: 3 }]
    expect(top3(subs).map((s) => s.name)).toEqual(["b", "d", "c"])
  })
})

describe("pie labels", () => {
  it("long names wrap at spaces so outside labels stay narrow", () => {
    expect(wrapName("Everyday activities")).toEqual(["Everyday", "activities"])
    expect(wrapName("Friends/Family")).toEqual(["Friends/Family"])
    expect(wrapName("Movies & series")).toEqual(["Movies &", "series"])
  })
  it("big slices keep the label inside, small ones move it outside", () => {
    expect(fitsInside(["Sleep"], 0.32, 100)).toBe(true)
    expect(fitsInside(["Learning"], 0.06, 100)).toBe(false)
    expect(fitsInside(["Everyday", "activities"], 0.2, 100)).toBe(true)
    expect(fitsInside(["Everyday", "activities"], 0.2, 60)).toBe(false)
  })
  it("slices under 3% get no label", () => {
    expect(MIN_LABEL_SHARE).toBe(0.03)
  })
  it("label text color reads on the slice color", () => {
    expect(textOn("#F2C14E")).toBe("#111111") // yellow
    expect(textOn("#A5A5A5")).toBe("#111111") // light gray
    expect(textOn("#2F343B")).toBe("#ffffff") // graphite
    expect(textOn("#E5322D")).toBe("#111111") // bright red: black has more contrast (4.8 vs 4.3)
    expect(textOn("#B71C1C")).toBe("#ffffff") // dark red
  })
})
