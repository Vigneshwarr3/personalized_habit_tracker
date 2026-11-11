/** Learned model: progress to the label goal, per-category precision/recall vs the routines, which categories it serves. */
import { useMemo } from "react"
import type { Data, Layout } from "plotly.js"
import { Check, Dumbbell } from "lucide-react"
import { Plot } from "@/components/Plot"
import { Button } from "@/components/ui/button"
import { Progress } from "@/components/ui/progress"
import { Skeleton } from "@/components/ui/skeleton"
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table"
import { Metric, PageTitle } from "@/components/common"
import { api } from "@/lib/api"
import { useAct } from "@/lib/actions"
import { useIsDark, useIsNarrow } from "@/lib/device"
import { useLabelStats, useModelStatus, useToday, type ModelStatus } from "@/lib/queries"
import { fmtTs, pct } from "@/lib/time"

interface Row {
  name: string; used: boolean; blocks: number
  rp: number; rr: number; mp: number; mr: number; rf1: number; mf1: number
}

function rowsOf(meta: ModelStatus): Row[] {
  return Object.entries(meta.metrics ?? {}).map(([cid, m]) => ({
    name: meta.names[cid] ?? cid, used: m.enabled, blocks: m.model.support,
    rp: m.routines.precision, rr: m.routines.recall, mp: m.model.precision, mr: m.model.recall,
    rf1: m.routines.f1, mf1: m.model.f1,
  })).sort((a, b) => b.blocks - a.blocks)
}

export function Model() {
  const stats = useLabelStats(), meta = useModelStatus()
  const { tz } = useToday()
  const { run, busy } = useAct()
  const m = meta.data
  const need = stats.data?.needed_for_model ?? 300, have = stats.data?.labeled_windows_approx ?? 0
  const acc = m?.accuracy ?? {}
  const rows = useMemo(() => (m ? rowsOf(m) : []), [m])

  return (
    <div className="space-y-5">
      <PageTitle title="Learned model">
        A LightGBM model trained on your labeled 15-minute blocks. It's tested on later days than it learned from, compared with your routines on the same blocks, and used only for categories where it's better. Your labels always win; routines remain the fallback. Retrains nightly when your labels change.
      </PageTitle>
      {!m || !stats.data ? <Skeleton className="h-28 w-full" /> : (
        <>
          <div className="flex flex-wrap gap-2">
            <Metric label="Labeled blocks" value={`${have} / ${need}`} />
            <Metric label="Model" value={m.serving ? "serving" : "off"} />
            <Metric label="Routines accuracy" value={pct(acc.routines)} help="On held-out later days." />
            <Metric label="With model" value={pct(acc.combined)}
              sub={acc.combined != null && acc.routines != null && `${(acc.combined - acc.routines) * 100 >= 0 ? "+" : ""}${Math.round((acc.combined - acc.routines) * 100)} pts`} />
          </div>
          {have < need && (
            <div className="max-w-2xl space-y-1.5">
              <p className="text-sm">Label {need - have} more blocks to start the model. Fastest way: Corrections → fix wrong rows → Confirm this day.</p>
              <Progress value={(100 * have) / need} aria-label="Labeled blocks toward the model" />
            </div>
          )}
          {m.trained_at && tz && (
            <p className="text-sm">
              Last trained {fmtTs(m.trained_at, tz)} on {m.n_labeled} labeled blocks
              {m.note && <> · {m.note}</>}
              {m.labels_changed_since_training && <> · <b>labels changed since then</b></>}
            </p>
          )}
        </>
      )}
      <Button className="h-10" disabled={busy}
        onClick={() => run(() => api.post("/model/train"), "Model trained.")}>
        <Dumbbell aria-hidden /> {busy ? "Training…" : "Train now"}
      </Button>
      {rows.length > 0 && <F1Chart rows={rows} />}
      {rows.length > 0 && (
        <div className="space-y-2">
          <div className="overflow-auto rounded-lg border">
            <Table className="text-sm">
              <TableHeader>
                <TableRow>
                  <TableHead>Category</TableHead><TableHead>Model used</TableHead><TableHead className="text-right">Blocks</TableHead>
                  <TableHead className="text-right">Routines precision</TableHead><TableHead className="text-right">Routines recall</TableHead>
                  <TableHead className="text-right">Model precision</TableHead><TableHead className="text-right">Model recall</TableHead>
                  <TableHead className="text-right">Routines F1</TableHead><TableHead className="text-right">Model F1</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {rows.map((r) => (
                  <TableRow key={r.name}>
                    <TableCell className="whitespace-nowrap">{r.name}</TableCell>
                    <TableCell>{r.used && <><Check className="size-4" aria-hidden /><span className="sr-only">yes</span></>}</TableCell>
                    {[r.blocks, r.rp, r.rr, r.mp, r.mr, r.rf1, r.mf1].map((x, i) => (
                      <TableCell key={i} className="text-right tabular-nums">{i === 0 ? x : x.toFixed(2)}</TableCell>
                    ))}
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>
          <p className="text-xs text-muted-foreground">Precision: when it says X, how often it's right. Recall: of your real X blocks, how many it finds. "Model used" = the model beat the routines for that category on later days.</p>
        </div>
      )}
    </div>
  )
}

function F1Chart({ rows }: { rows: Row[] }) {
  const dark = useIsDark(), narrow = useIsNarrow()
  const { data, layout } = useMemo(() => {
    const ink = dark ? "#e6e8eb" : "#1d2127"
    const y = rows.map((r) => r.name)
    const data: Data[] = [
      { type: "bar", orientation: "h", name: "Routines", y, x: rows.map((r) => r.rf1), marker: { color: "#A5A5A5" }, hovertemplate: "%{y}: routines F1 %{x:.2f}<extra></extra>" },
      { type: "bar", orientation: "h", name: "Model", y, x: rows.map((r) => r.mf1), marker: { color: "#6E98D2" }, hovertemplate: "%{y}: model F1 %{x:.2f}<extra></extra>" },
    ] as Data[]
    const layout: Partial<Layout> = {
      barmode: "group", bargap: 0.3, bargroupgap: 0.1, margin: { l: 8, r: 8, t: 70, b: 8 },
      title: { text: "F1 per category (held-out days)", font: { size: 14 }, x: 0, y: 0.98, yanchor: "top" },
      xaxis: { range: [0, 1], tickformat: ".0%" }, yaxis: { autorange: "reversed", automargin: true },
      legend: { orientation: "h", x: 0, y: 1, yanchor: "bottom" },
      font: { family: "Inter Variable, system-ui, sans-serif", color: ink, size: narrow ? 11 : 12 },
      paper_bgcolor: "rgba(0,0,0,0)", plot_bgcolor: "rgba(0,0,0,0)",
    }
    return { data, layout }
  }, [rows, dark, narrow])
  return <Plot data={data} layout={layout} height={120 + 46 * rows.length} className="max-w-4xl"
    ariaLabel="F1 per category for routines and the model; the table below has the same numbers." />
}
