/** Shown when a page can't load and there's no saved data to fall back on, or when a page crashes. */
import { useState } from "react"
import { Link, isRouteErrorResponse, useLocation, useNavigate, useRouteError } from "react-router"
import { useQueryClient } from "@tanstack/react-query"
import { ChevronDown, Home, RotateCw, SearchX, ServerCrash, ShieldAlert, TriangleAlert, WifiOff } from "lucide-react"
import { Button } from "@/components/ui/button"
import { errorKind } from "@/lib/queryClient"
import { ApiError } from "@/lib/api"

const TEXT = {
  offline: {
    icon: WifiOff, title: "Can't reach your server",
    body: "Check that Tailscale is connected on this device, then try again. Nothing was saved on this device for this page yet, so there's nothing to show offline.",
  },
  private: {
    icon: ShieldAlert, title: "This dashboard is private",
    body: "It only opens for its owner's Tailscale login. Check that this device is signed in to Tailscale with your account.",
  },
  server: {
    icon: ServerCrash, title: "The server had a problem",
    body: "Your data is safe; this page just couldn't be loaded. Try again in a moment.",
  },
  bug: {
    icon: TriangleAlert, title: "Something went wrong on this page",
    body: "Your data is safe. Try again; if it keeps happening, the details below say what broke.",
  },
}

function details(e: unknown): string {
  const build = `\n\nApp version: built ${__BUILD_TIME__}`
  if (e instanceof ApiError) return `${e.status} on ${e.path}: ${e.message}${build}`
  if (isRouteErrorResponse(e)) return `${e.status} ${e.statusText}${build}`
  if (e instanceof Error) return `${e.name}: ${e.message}${build}${e.stack ? `\n\n${e.stack}` : ""}`
  return String(e) + build
}

export function ErrorView({ error }: { error: unknown }) {
  const qc = useQueryClient()
  const navigate = useNavigate()
  const loc = useLocation()
  const [open, setOpen] = useState(false)
  const t = TEXT[errorKind(error)]

  const retry = async () => {
    await qc.resetQueries({ predicate: (q) => q.state.status === "error" })
    navigate(`${loc.pathname}${loc.search}`, { replace: true }) // re-renders the page
  }

  return (
    <section className="mx-auto mt-10 max-w-lg text-center" aria-labelledby="err-title">
      <t.icon className="mx-auto size-10 text-muted-foreground" aria-hidden />
      <h1 id="err-title" className="mt-4 text-2xl font-semibold">{t.title}</h1>
      <p className="mt-2 text-muted-foreground">{t.body}</p>
      <div className="mt-6 flex flex-wrap justify-center gap-2">
        <Button onClick={retry} className="h-11 px-5"><RotateCw aria-hidden /> Try again</Button>
        <Button variant="outline" className="h-11 px-5" asChild>
          <Link to="/"><Home aria-hidden /> Go to Overview</Link>
        </Button>
      </div>
      <div className="mt-6 text-left">
        <button
          type="button" onClick={() => setOpen(!open)} aria-expanded={open}
          className="mx-auto flex min-h-11 items-center gap-1 rounded-md px-2 text-sm text-muted-foreground hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none"
        >
          <ChevronDown className={`size-4 transition-transform ${open ? "rotate-180" : ""}`} aria-hidden /> Show details
        </button>
        {open && (
          <pre className="mt-2 max-h-72 overflow-auto rounded-lg border bg-muted p-3 text-xs whitespace-pre-wrap [overflow-wrap:anywhere]">
            {details(error)}
          </pre>
        )}
      </div>
    </section>
  )
}

export function RouteErrorPage() {
  return <ErrorView error={useRouteError()} />
}

export function NotFound() {
  return (
    <section className="mx-auto mt-10 max-w-lg text-center" aria-labelledby="nf-title">
      <SearchX className="mx-auto size-10 text-muted-foreground" aria-hidden />
      <h1 id="nf-title" className="mt-4 text-2xl font-semibold">Page not found</h1>
      <p className="mt-2 text-muted-foreground">There's no page at this address.</p>
      <Button className="mt-6 h-11 px-5" asChild><Link to="/"><Home aria-hidden /> Go to Overview</Link></Button>
    </section>
  )
}
