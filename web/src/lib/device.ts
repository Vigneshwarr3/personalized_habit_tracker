import { useSyncExternalStore } from "react"

/** True inside the Habit Tracker Android app (it adds "HabitTrackerApp/<version>" to its user agent). */
export const inApp = (): boolean => navigator.userAgent.includes("HabitTrackerApp")

export function useMediaQuery(query: string): boolean {
  return useSyncExternalStore(
    (cb) => {
      const m = window.matchMedia(query)
      m.addEventListener("change", cb)
      return () => m.removeEventListener("change", cb)
    },
    () => window.matchMedia(query).matches,
    () => false,
  )
}

/** Phone-sized screen (layout stacks). */
export const useIsNarrow = () => useMediaQuery("(max-width: 767px)")
/** Touch screen (tap instead of drag on charts). */
export const useIsTouch = () => useMediaQuery("(pointer: coarse)")
export const useIsDark = () => useMediaQuery("(prefers-color-scheme: dark)")
