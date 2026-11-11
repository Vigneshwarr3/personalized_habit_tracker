import {
  BrainCircuit, CalendarDays, Lock, Network, Palette, PencilLine, Settings, type LucideIcon,
} from "lucide-react"

export interface PageInfo {
  path: string // under /ui/
  title: string
  icon: LucideIcon
}

export const PAGES: PageInfo[] = [
  { path: "", title: "Overview", icon: CalendarDays },
  { path: "corrections", title: "Corrections", icon: PencilLine },
  { path: "categories", title: "Categories", icon: Palette },
  { path: "knowledge-graph", title: "Knowledge graph", icon: Network },
  { path: "model", title: "Model", icon: BrainCircuit },
  { path: "privacy", title: "Privacy & devices", icon: Lock },
  { path: "settings", title: "Settings", icon: Settings },
]

/** The page for a router path. The router gives paths without the /ui basename ("/settings"). */
export function pageFor(pathname: string): PageInfo | undefined {
  const seg = pathname.replace(/^\/+/, "").split("/")[0]
  return PAGES.find((p) => p.path === seg)
}
