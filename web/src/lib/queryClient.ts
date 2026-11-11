/** Data fetching + the copy of your data saved on this device.
 *
 * Stale-while-revalidate: every page first shows the last data it saw (even after the app was closed), then
 * refreshes in the background and swaps the new data in. If the refresh fails and there is saved data, the page
 * keeps showing it with an "offline" banner; only when there is nothing saved does the error page appear. */
import { QueryClient } from "@tanstack/react-query"
import { createSyncStoragePersister } from "@tanstack/query-sync-storage-persister"
import { removeOldestQuery } from "@tanstack/react-query-persist-client"
import { ApiError, isNetworkError } from "./api"

export const SAVED_DATA_MAX_AGE = 7 * 24 * 60 * 60 * 1000 // saved data older than 7 days is ignored

/** Bump when API response shapes change, so old saved data is thrown away instead of crashing a page. */
export const CACHE_VERSION = "1"

export const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 30_000, // like the old dashboard's 30 s cache; after that, opening a page refreshes it
      gcTime: SAVED_DATA_MAX_AGE, // must be >= the saved-data age or saved entries get dropped early
      networkMode: "always", // always try: Tailscale can be up even when the browser thinks it's offline
      retry: (failures, err) => isNetworkError(err) && failures < 2, // Tailscale may need a moment to reconnect
      retryDelay: (n) => 1500 * (n + 1),
      refetchOnWindowFocus: true,
      // Error page only when there is nothing to show; otherwise keep the saved data on screen.
      throwOnError: (_err, query) => query.state.data === undefined,
    },
    mutations: { networkMode: "always", retry: false },
  },
})

function storage(): Storage | undefined {
  try {
    const s = window.localStorage
    s.setItem("ht-probe", "1")
    s.removeItem("ht-probe")
    return s
  } catch {
    return undefined // private window / blocked storage: the app still works, it just won't remember
  }
}

export const persister = createSyncStoragePersister({
  storage: storage(),
  key: "habit-tracker-cache",
  throttleTime: 1000,
  retry: removeOldestQuery, // storage full: drop the oldest saved page and try again
})

export function errorKind(e: unknown): "private" | "offline" | "server" | "bug" {
  if (e instanceof ApiError && (e.status === 401 || e.status === 403)) return "private"
  if (isNetworkError(e)) return "offline"
  if (e instanceof ApiError) return "server"
  return "bug"
}
