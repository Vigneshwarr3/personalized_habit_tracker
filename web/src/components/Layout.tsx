import { useEffect, useRef } from "react"
import { NavLink, Outlet, useLocation } from "react-router"
import { useIsFetching } from "@tanstack/react-query"
import { LoaderCircle, Menu, Smartphone } from "lucide-react"
import { Button } from "@/components/ui/button"
import {
  DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuSeparator, DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import { cn } from "@/lib/utils"
import { inApp, useIsNarrow } from "@/lib/device"
import { PAGES, pageFor } from "@/pages"
import { Toaster } from "@/components/ui/sonner"
import { StaleBanner } from "./StaleBanner"

function Refreshing() {
  const n = useIsFetching()
  return (
    <span role="status" aria-live="polite" className="inline-flex size-5 items-center justify-center text-muted-foreground">
      {n > 0 && <LoaderCircle className="size-4 animate-spin" aria-label="Refreshing" />}
    </span>
  )
}


export function Layout() {
  const narrow = useIsNarrow()
  const app = inApp()
  const { pathname } = useLocation()
  const page = pageFor(pathname)
  const main = useRef<HTMLElement>(null)

  useEffect(() => {
    document.title = page ? `${page.title} · Habit Tracker` : "Habit Tracker"
    main.current?.focus({ preventScroll: true }) // screen readers: new page starts here
  }, [page])

  const menu = narrow || app
  return (
    <div className="mx-auto flex min-h-dvh max-w-[1500px] flex-col px-4 pb-10 sm:px-6">
      <a href="#main" className="sr-only focus:not-sr-only focus:absolute focus:z-50 focus:rounded-md focus:bg-background focus:p-2">
        Skip to content
      </a>
      <header className="flex items-center gap-3 border-b py-2.5">
        <span className="font-semibold whitespace-nowrap">
          Habit Tracker{menu && page && <span className="font-normal text-muted-foreground"> · {page.title}</span>}
        </span>
        <Refreshing />
        <nav aria-label="Pages" className="ml-auto">
          {menu ? (
            <DropdownMenu>
              <DropdownMenuTrigger asChild>
                <Button variant="ghost" size="icon" className="size-11" aria-label="Open page menu">
                  <Menu className="size-5" />
                </Button>
              </DropdownMenuTrigger>
              <DropdownMenuContent align="end" className="min-w-56">
                {PAGES.map((p) => (
                  <DropdownMenuItem key={p.path} asChild className="min-h-11">
                    <NavLink to={`/${p.path}`} end>
                      <p.icon aria-hidden /> {p.title}
                    </NavLink>
                  </DropdownMenuItem>
                ))}
                {app && (
                  <>
                    <DropdownMenuSeparator />
                    <DropdownMenuItem asChild className="min-h-11">
                      <a href="habittracker://settings"><Smartphone aria-hidden /> App settings &amp; sync</a>
                    </DropdownMenuItem>
                  </>
                )}
              </DropdownMenuContent>
            </DropdownMenu>
          ) : (
            <ul className="flex flex-wrap justify-end gap-1">
              {PAGES.map((p) => (
                <li key={p.path}>
                  <NavLink
                    to={`/${p.path}`}
                    end
                    className={({ isActive }) => cn(
                      "inline-flex h-9 items-center gap-1.5 rounded-md px-2.5 text-sm transition-colors",
                      "hover:bg-accent focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none",
                      isActive ? "bg-accent font-medium text-foreground" : "text-muted-foreground",
                    )}
                  >
                    <p.icon className="size-4" aria-hidden /> {p.title}
                  </NavLink>
                </li>
              ))}
            </ul>
          )}
        </nav>
      </header>
      <StaleBanner />
      <main id="main" ref={main} tabIndex={-1} className="flex-1 pt-4 outline-none">
        <Outlet />
      </main>
      <Toaster position={narrow ? "top-center" : "bottom-right"} closeButton />
    </div>
  )
}
