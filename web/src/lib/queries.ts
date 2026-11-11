/** Typed API calls used by the pages. Query keys are the cache keys for the saved data. */
import { keepPreviousData, useQuery } from "@tanstack/react-query"
import { api } from "./api"
import { todayIn } from "./time"

export interface Status {
  paused: boolean
  server_time: number
  timezone: string
  you: { role: string; device: string | null }
}

export interface Category {
  id: number
  name: string
  short_name: string
  color: string
  archived: boolean
  merged_into: number | null
  sort_order: number
  parent_id: number | null
  is_activity: boolean
  color_custom: boolean
}

export interface HeatCell {
  date: string
  hour: number
  category_id: number | null
  category: string
  color: string | null
  minutes: Record<string, number>
  devices: string[]
  places: string[]
}

export interface SummaryRow {
  category_id: number
  category: string
  color: string
  hours: number
  activity_id: number | null
  activity: string | null
  activity_color: string | null
}

export interface Dashboard {
  timezone: string
  start: string
  end: string
  dates: string[]
  heatmap: HeatCell[]
  summary: { categories: SummaryRow[]; classified_hours: number; unclassified_hours: number }
}

interface Hours { hours: number; prev_hours: number }

export interface Kpis {
  cards: Record<string, boolean>
  top3: (Hours & { category_id: number; category: string; short_name: string; color: string })[]
  group_totals: (Hours & { group: string; activity_id: number; color: string })[]
  time_to_cut: (Hours & { group: string }) | null
  coverage: { filled_blocks: number; total_blocks: number; percent: number | null }
  phone_vs_laptop: { phone_hours: number; laptop_hours: number }
  commute: Hours
  focus: { hours: number; start_ts: number | null; end_ts: number | null; date: string | null }
  streak: { rule: string; current_days: number; best_days: number; today_pending: boolean }
}

export const useStatus = () => useQuery({ queryKey: ["status"], queryFn: () => api.get<Status>("/status") })

export const useCategories = (includeHidden = false) =>
  useQuery({
    queryKey: ["categories", { includeHidden }],
    queryFn: () => api.get<Category[]>("/categories", { include_hidden: includeHidden }),
  })

/** A date range for the Overview. `key` names it for the saved copy ("last7" etc.), so reopening the app on a
 * new day still shows yesterday's saved "last 7 days" first, then refreshes to the new dates. */
export interface Range { key: string; start: string; end: string }

export const useDashboard = (r: Range | undefined) =>
  useQuery({
    queryKey: ["dashboard", r?.key],
    queryFn: () => api.get<Dashboard>("/dashboard", { start: r!.start, end: r!.end }),
    enabled: !!r,
  })

export const useKpis = (r: Range | undefined) =>
  useQuery({
    queryKey: ["kpis", r?.key],
    queryFn: () => api.get<Kpis>("/kpis", { start: r!.start, end: r!.end }),
    enabled: !!r,
  })

/** Everything the server knows about the tracker's timezone + "today" there. */
export function useToday() {
  const status = useStatus()
  const tz = status.data?.timezone
  return { tz, today: tz ? todayIn(tz) : undefined, status }
}

// ---- corrections
export interface TopItem { app: string; app_name: string | null; domain: string | null; title: string | null; signal: string; minutes: number }
export interface Window {
  window_start: number
  window_end: number
  category_id: number | null
  category: string | null
  color: string | null
  source: string
  routine: string | null
  confidence: number | null
  features: { devices?: string[]; top_items?: TopItem[] } | null
}
export interface LocationState { label?: string; state?: string; place_id?: number }
export interface Windows { window_s: number; windows: Window[]; locations: Record<string, LocationState> }
export interface Label { id: number; start_ts: number; end_ts: number; category_id: number; note: string | null; created_at: number }
export interface LabelStats { labeled_windows_approx: number; needed_for_model: number }
export interface Place {
  id: number; name: string | null; label: string; category_id: number | null; osm_url: string
  osm_suggestion: { name?: string; type?: string; address?: string } | null
}

export const useLabelable = () => useQuery({ queryKey: ["categories", "labelable"], queryFn: () => api.get<Category[]>("/categories/labelable") })
export const useWindows = (day?: string) => useQuery({
  queryKey: ["windows", day], enabled: !!day, queryFn: () => api.get<Windows>("/windows", { start: day, end: day }),
})
export const useLabels = (day?: string) => useQuery({
  queryKey: ["labels", day], enabled: !!day, queryFn: () => api.get<Label[]>("/labels", { start: day, end: day }),
})
export const useLabelStats = () => useQuery({ queryKey: ["labels", "stats"], queryFn: () => api.get<LabelStats>("/labels/stats") })
export const usePlaces = (enabled: boolean) => useQuery({ queryKey: ["places"], enabled, queryFn: () => api.get<Place[]>("/places") })

// ---- settings
export interface UiSettings {
  cards: Record<string, boolean>
  card_labels: Record<string, string>
  time_to_cut_activity: number | null
  streak: { activity: number | null; min_hours: number }
  focus: { activities: number[]; categories: number[] }
  sessions: { binge_hours: number; doom_minutes: number }
  location: { travel_km: number; precision: "100m" | "1km"; radius_m: number; autofill_places: boolean }
}
export const useUiSettings = () => useQuery({ queryKey: ["ui-settings"], queryFn: () => api.get<UiSettings>("/ui-settings") })

// ---- privacy & devices
export interface HiddenRange { id: number; start_ts: number; end_ts: number; reason: string | null; unhidden_at: number | null }
export interface Device { name: string; source: string; created_at: number; revoked_at: number | null; last_seen: number | null }
export interface Stats {
  db_bytes: number; paused: boolean
  devices: { device: string; source: string; spans: number; samples: number; first_ts: number; last_ts: number }[]
}
export const useHidden = () => useQuery({ queryKey: ["hidden-ranges"], queryFn: () => api.get<HiddenRange[]>("/hidden-ranges") })
export const useDevices = () => useQuery({ queryKey: ["devices"], queryFn: () => api.get<Device[]>("/devices") })
export const useStats = () => useQuery({ queryKey: ["stats"], queryFn: () => api.get<Stats>("/stats") })
/** The Android app published by deploy.sh (404 = none). Never shows the error page. */
export const useApk = () => useQuery({
  queryKey: ["apk"], retry: false, throwOnError: false,
  queryFn: () => api.get<{ version_name: string }>("/app/android/version"),
})

// ---- knowledge graph
export interface KgStats {
  nodes: Record<string, number>; lookups: Record<string, number>; queue: Record<string, number>
  llm_calls_saved: number; cache_hit_rate: number | null; llm_calls_today: number; daily_budget: number
  dry_run: boolean; provider: string | null; llm_ready: boolean; embedder: string | null
}
export interface KgItem {
  node_id: number; type: string; label: string; domain: string | null; channel: string | null; seen_count: number
  signal: string | null; confidence: number | null; source: string | null; edge_id: number | null; reason: string | null
  queue_status: string | null; queue_error: string | null; last_seen: number
}
export interface KgLookup { id: number; ts: number; outcome: string; type: string | null; label: string | null; signal: string | null; confidence: number | null; detail: string | null }
export const useKgStats = (days?: number) => useQuery({ queryKey: ["kg", "stats", days ?? "all"], queryFn: () => api.get<KgStats>("/kg/stats", { days }) })
export const useKgReview = () => useQuery({ queryKey: ["kg", "review"], queryFn: () => api.get<KgItem[]>("/kg/review") })
export const useKgNodes = (q: string, type: string) => useQuery({
  queryKey: ["kg", "nodes", q, type], placeholderData: keepPreviousData,
  queryFn: () => api.get<KgItem[]>("/kg/nodes", { q: q || null, type: type || null, limit: 300 }),
})
export const useKgLookups = () => useQuery({ queryKey: ["kg", "lookups"], queryFn: () => api.get<KgLookup[]>("/kg/lookups", { limit: 200 }) })
export const useSignals = () => useQuery({ queryKey: ["signals"], queryFn: () => api.get<{ signals: string[] }>("/signals") })

// ---- model
interface Prf { precision: number; recall: number; f1: number; support: number }
export interface ModelStatus {
  serving: boolean; trained_at?: number; n_labeled?: number; note?: string; labels_changed_since_training?: boolean
  accuracy?: { routines?: number | null; combined?: number | null }
  metrics?: Record<string, { enabled: boolean; model: Prf; routines: Prf }>
  names: Record<string, string>
}
export const useModelStatus = () => useQuery({ queryKey: ["model"], queryFn: () => api.get<ModelStatus>("/model/status") })
