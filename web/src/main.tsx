import { StrictMode } from "react"
import { createRoot } from "react-dom/client"
import { createBrowserRouter, RouterProvider } from "react-router"
import { PersistQueryClientProvider } from "@tanstack/react-query-persist-client"
import { registerSW } from "virtual:pwa-register"
import "./index.css"
import { CACHE_VERSION, SAVED_DATA_MAX_AGE, persister, queryClient } from "@/lib/queryClient"
import { Layout } from "@/components/Layout"
import { NotFound, RouteErrorPage } from "@/pages/ErrorPage"
import { Overview } from "@/pages/overview/Overview"
import { Corrections } from "@/pages/corrections/Corrections"
import { Categories } from "@/pages/Categories"
import { KnowledgeGraph } from "@/pages/KnowledgeGraph"
import { Model } from "@/pages/Model"
import { Privacy } from "@/pages/Privacy"
import { Settings } from "@/pages/Settings"

const router = createBrowserRouter(
  [
    {
      element: <Layout />,
      errorElement: <RouteErrorPage />, // crash outside a page (e.g. the header)
      children: [
        {
          errorElement: <RouteErrorPage />, // a page failed: header + menu stay usable
          children: [
            { index: true, element: <Overview /> },
            { path: "corrections", element: <Corrections /> },
            { path: "categories", element: <Categories /> },
            { path: "knowledge-graph", element: <KnowledgeGraph /> },
            { path: "model", element: <Model /> },
            { path: "privacy", element: <Privacy /> },
            { path: "settings", element: <Settings /> },
            { path: "*", element: <NotFound /> },
          ],
        },
      ],
    },
  ],
  { basename: "/ui" },
)

// Keeps the app's files on the device (opens offline). A new version is looked for when the app is opened, when you
// come back to it, and when you switch pages (at most every 30 s); once it's installed the page reloads into it.
// Without the last two, an app left open kept showing the old version after a deploy.
if (import.meta.env.PROD) registerSW({
  immediate: true,
  onRegisteredSW(_url, reg) {
    if (!reg) return
    let last = Date.now()
    const check = () => {
      if (Date.now() - last < 30_000) return
      last = Date.now()
      reg.update().catch(() => { /* offline: try again next time */ })
    }
    document.addEventListener("visibilitychange", () => { if (document.visibilityState === "visible") check() })
    window.addEventListener("focus", check)
    router.subscribe(check)
  },
})

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <PersistQueryClientProvider
      client={queryClient}
      persistOptions={{ persister, maxAge: SAVED_DATA_MAX_AGE, buster: CACHE_VERSION }}
    >
      <RouterProvider router={router} />
    </PersistQueryClientProvider>
  </StrictMode>,
)
