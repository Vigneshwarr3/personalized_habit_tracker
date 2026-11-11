/** Plotly chart. The library (~1 MB) loads on first use, so the app itself opens fast. */
import { useEffect, useRef, useState } from "react"
import type { Config, Data, Layout, PlotlyHTMLElement, PlotMouseEvent, PlotSelectionEvent } from "plotly.js"
import { Skeleton } from "@/components/ui/skeleton"

type PlotlyLib = typeof import("plotly.js")
let lib: Promise<PlotlyLib> | null = null
const loadPlotly = () => (lib ??= import("plotly.js-cartesian-dist-min").then((m) => m.default as PlotlyLib))

interface Props {
  data: Data[]
  layout: Partial<Layout>
  config?: Partial<Config>
  height: number
  className?: string
  ariaLabel: string
  onClick?: (e: PlotMouseEvent) => void
  onSelected?: (e: PlotSelectionEvent | undefined) => void
}

export function Plot({ data, layout, config, height, className, ariaLabel, onClick, onSelected }: Props) {
  const el = useRef<HTMLDivElement>(null)
  const [plotly, setPlotly] = useState<PlotlyLib | null>(null)
  const [failed, setFailed] = useState<unknown>(null)
  const handlers = useRef({ onClick, onSelected })
  handlers.current = { onClick, onSelected }
  const box = useRef<HTMLDivElement>(null)
  const [width, setWidth] = useState(0)

  // Size the chart to its container ourselves (Plotly's own window-resize handling could leave it squashed
  // after a rotation or keyboard pop-up).
  useEffect(() => {
    const b = box.current
    if (!b) return
    const ro = new ResizeObserver(([e]) => setWidth(Math.floor(e.contentRect.width)))
    ro.observe(b)
    return () => ro.disconnect()
  }, [])

  useEffect(() => {
    let alive = true
    loadPlotly().then((p) => alive && setPlotly(p), (e) => alive && setFailed(e))
    return () => { alive = false }
  }, [])
  const bound = useRef(false)
  useEffect(() => {
    const div = el.current as unknown as PlotlyHTMLElement | null
    if (!plotly || !div || width <= 0) return
    plotly.react(div, data, { ...layout, width, height, autosize: false },
      { displayModeBar: false, responsive: false, ...config }).then(() => {
      // events exist only once Plotly has drawn the chart; bind once (handlers are read through a ref)
      if (bound.current || !div.on) return
      bound.current = true
      div.on("plotly_click", (e) => handlers.current.onClick?.(e))
      div.on("plotly_selected", (e) => handlers.current.onSelected?.(e))
    })
  }, [plotly, data, layout, config, height, width])

  useEffect(() => {
    const div = el.current
    return () => { if (div && lib && bound.current) lib.then((p) => p.purge(div)) }
  }, [])

  if (failed) throw failed // shows the error page (e.g. the chart code couldn't be downloaded)

  return (
    <div ref={box} className={className} style={{ height }} role="img" aria-label={ariaLabel}>
      {!plotly && <Skeleton className="h-full w-full" />}
      <div ref={el} style={{ height, display: plotly ? "block" : "none" }} />
    </div>
  )
}
