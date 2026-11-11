/** Knowledge graph: cache-hit stats, review queue, browse/edit classifications of unknown items. */
import { useDeferredValue, useMemo, useState } from "react"
import type { Data, Layout } from "plotly.js"
import { Pencil, Play, RotateCw, Trash2 } from "lucide-react"
import { Plot } from "@/components/Plot"
import { Button } from "@/components/ui/button"
import { Card } from "@/components/ui/card"
import { Checkbox } from "@/components/ui/checkbox"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Skeleton } from "@/components/ui/skeleton"
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { Empty, Field, Metric, PageTitle, SimpleSelect } from "@/components/common"
import { api } from "@/lib/api"
import { useAct } from "@/lib/actions"
import { useIsDark } from "@/lib/device"
import { useKgLookups, useKgNodes, useKgReview, useKgStats, useSignals, useToday, type KgItem } from "@/lib/queries"
import { fmtTs, pct } from "@/lib/time"

const ORDER = ["exact", "parent", "semantic", "llm", "queued", "local-only"]
const NAMES: Record<string, string> = {
  exact: "Exact item", parent: "Same channel / site", semantic: "Similar title", llm: "Asked LLM",
  queued: "Queued (LLM unavailable)", "local-only": "Search (never sent, local only)",
}
const KIND: Record<string, string> = { video: "YouTube video", page: "web page", title: "browser tab", search: "search", app: "app" }

export function KnowledgeGraph() {
  const stats = useKgStats(), week = useKgStats(7), review = useKgReview()
  const { tz } = useToday()
  const { run, busy } = useAct()
  const s = stats.data

  return (
    <div className="space-y-5">
      <PageTitle title="Knowledge graph">
        Items your rules don't know are classified here: exact match → same channel/site → similar title → LLM (title, site and channel name only). Every answer is stored, so the LLM is asked once per item.
      </PageTitle>
      {!s ? <Skeleton className="h-28 w-full" /> : (
        <>
          <div className="flex flex-wrap gap-2">
            <Metric label="Served without LLM" value={pct(s.cache_hit_rate)} help="Exact + channel/site + similar-title hits, divided by those plus LLM answers." />
            <Metric label="LLM calls saved" value={s.llm_calls_saved.toLocaleString()} sub={week.data && `${week.data.llm_calls_saved.toLocaleString()} this week`} />
            <Metric label="LLM calls today" value={`${s.llm_calls_today} / ${s.daily_budget}`} />
            <Metric label="Items in graph" value={Object.values(s.nodes).reduce((a, b) => a + b, 0).toLocaleString()} />
            <Metric label="Needs review" value={review.data?.length ?? "—"} />
          </div>
          <p className="text-sm">
            {[s.llm_ready ? <>LLM: <b>{s.provider}</b></> : <>LLM: <b>no API key</b> (items wait in the queue)</>,
              s.dry_run && <><b>dry-run on</b> (LLM never called)</>,
              <>similarity model: {s.embedder ?? "off"}</>,
              <>queue: {s.queue.pending ?? 0} pending, {s.queue.failed ?? 0} failed</>,
            ].filter(Boolean).map((x, i) => <span key={i}>{i > 0 && " · "}{x}</span>)}
          </p>
          <LookupChart lookups={s.lookups} />
        </>
      )}
      <Button variant="outline" className="h-10" disabled={busy}
        onClick={() => run(() => api.post<Record<string, number>>("/kg/process"),
          (r) => `Processed: ${Object.entries(r).map(([k, v]) => `${k} ${v}`).join(", ") || "nothing to do"}.`)}>
        <Play aria-hidden /> Process queue now
      </Button>

      <Tabs defaultValue="review">
        <TabsList className="h-auto flex-wrap">
          <TabsTrigger value="review">Review queue ({review.data?.length ?? "…"})</TabsTrigger>
          <TabsTrigger value="browse">Browse &amp; edit</TabsTrigger>
          <TabsTrigger value="log">Recent lookups</TabsTrigger>
        </TabsList>
        <TabsContent value="review" className="space-y-2">
          {!review.data ? <Skeleton className="h-40 w-full" /> : review.data.length === 0 ? <Empty>Nothing to review.</Empty>
            : review.data.slice(0, 50).map((it) => (
              <Card key={it.node_id} size="sm" className="gap-2 px-4">
                <p className="font-semibold [overflow-wrap:anywhere]">{it.label}</p>
                <Describe item={it} />
                <Editor item={it} />
              </Card>
            ))}
        </TabsContent>
        <TabsContent value="browse"><Browse tz={tz} /></TabsContent>
        <TabsContent value="log"><Lookups tz={tz} /></TabsContent>
      </Tabs>
    </div>
  )
}

function LookupChart({ lookups }: { lookups: Record<string, number> }) {
  const dark = useIsDark()
  const counts = ORDER.map((k) => lookups[k] ?? 0)
  const { data, layout } = useMemo(() => {
    const ink = dark ? "#e6e8eb" : "#1d2127"
    const data: Data[] = [{
      type: "bar", orientation: "h", y: ORDER.map((k) => NAMES[k]), x: counts, marker: { color: "#6E98D2" },
      text: counts.map(String), textposition: "outside", cliponaxis: false, hovertemplate: "%{y}: %{x}<extra></extra>",
    } as Data]
    const layout: Partial<Layout> = {
      margin: { l: 8, r: 40, t: 28, b: 8 }, title: { text: "How lookups were answered (all time)", font: { size: 14 }, x: 0 },
      yaxis: { autorange: "reversed", automargin: true }, xaxis: { showgrid: false, visible: false },
      font: { family: "Inter Variable, system-ui, sans-serif", color: ink }, paper_bgcolor: "rgba(0,0,0,0)", plot_bgcolor: "rgba(0,0,0,0)",
    }
    return { data, layout }
  }, [counts.join(","), dark]) // eslint-disable-line react-hooks/exhaustive-deps
  if (!counts.some(Boolean)) return null
  return <Plot data={data} layout={layout} height={230} className="max-w-3xl"
    ariaLabel={`How lookups were answered: ${ORDER.map((k, i) => `${NAMES[k]} ${counts[i]}`).join(", ")}`} />
}

function Describe({ item }: { item: KgItem }) {
  const where = [item.channel, item.domain].filter(Boolean).join(" · ")
  return (
    <p className="text-sm [overflow-wrap:anywhere]">
      <span className="text-muted-foreground">{KIND[item.type] ?? item.type}{where && ` · ${where}`}</span><br />
      {item.signal ? (
        <><b>{item.signal}</b> ({item.confidence?.toFixed(2)}, {item.source}){item.reason && ` — ${item.reason}`}</>
      ) : (
        <><b>unknown</b> ({item.queue_status ?? "not queued"}{item.queue_error && `: ${item.queue_error}`})</>
      )}
    </p>
  )
}

/** Signal picker + "apply to whole channel/site" + save / remove / retry. */
function Editor({ item }: { item: KgItem }) {
  const signals = useSignals().data?.signals ?? []
  const { run, busy } = useAct()
  const [chosen, setSig] = useState<string | null>(null)
  const sig = chosen ?? (item.signal && signals.includes(item.signal) ? item.signal : "other")
  const [parent, setParent] = useState(false)
  const where = item.channel || item.domain
  const canParent = !!where && !["app", "title", "search"].includes(item.type)
  const cb = `parent-${item.node_id}`
  return (
    <div className="flex flex-wrap items-end gap-3">
      <Field label="Signal" className="w-56">
        {(id) => <SimpleSelect id={id} value={sig} onChange={setSig} options={signals.map((x) => ({ value: x, label: x }))} />}
      </Field>
      <div className="flex min-h-10 items-center gap-2" title="New items from the same channel/site will inherit this signal.">
        <Checkbox id={cb} checked={parent} disabled={!canParent} onCheckedChange={(v) => setParent(v === true)} />
        <Label htmlFor={cb}>{item.channel ? "Apply to the whole channel" : "Apply to the whole site"}</Label>
      </div>
      <Button className="h-10" disabled={busy}
        onClick={() => run(() => api.post("/kg/correct", { node_id: item.node_id, signal: sig, apply_to_parent: parent }), "Saved.")}>Save</Button>
      {item.edge_id && item.source !== "parent" && (
        <Button variant="outline" className="h-10" disabled={busy} title="Kept in history; the item becomes unknown."
          onClick={() => run(() => api.post(`/kg/edges/${item.edge_id}/deactivate`), "Classification removed (kept in history).")}>
          <Trash2 aria-hidden /> Remove classification
        </Button>
      )}
      {item.queue_status === "failed" && (
        <Button variant="outline" className="h-10" disabled={busy}
          onClick={() => run(() => api.post(`/kg/nodes/${item.node_id}/retry`), "Queued again.")}>
          <RotateCw aria-hidden /> Retry LLM
        </Button>
      )}
    </div>
  )
}

const TYPES = ["", "video", "page", "search", "title", "app", "channel", "path", "domain"]

function Browse({ tz }: { tz?: string }) {
  const [q, setQ] = useState(""), [type, setType] = useState("")
  const dq = useDeferredValue(q.trim())
  const nodes = useKgNodes(dq, type)
  const [edit, setEdit] = useState<number | null>(null)
  const picked = nodes.data?.find((n) => n.node_id === edit)
  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-end gap-3">
        <Field label="Search title / site / app" className="min-w-64 flex-1">
          {(id) => <Input id={id} type="search" className="h-10" value={q} onChange={(e) => setQ(e.target.value)} />}
        </Field>
        <Field label="Type" className="w-44">
          {(id) => <SimpleSelect id={id} value={type || "any"} onChange={(v) => setType(v === "any" ? "" : v)}
            options={TYPES.map((t) => ({ value: t || "any", label: t || "any" }))} />}
        </Field>
      </div>
      {picked && (
        <Card size="sm" className="gap-2 border-ring px-4">
          <p className="font-semibold [overflow-wrap:anywhere]">Editing: {picked.label}</p>
          <Describe item={picked} />
          <Editor key={picked.node_id} item={picked} />
        </Card>
      )}
      {!nodes.data ? <Skeleton className="h-60 w-full" /> : nodes.data.length === 0 ? <Empty>No items found.</Empty> : (
        <div className="max-h-[520px] overflow-auto rounded-lg border">
          <Table className="text-sm">
            <TableHeader className="sticky top-0 bg-background">
              <TableRow>
                <TableHead className="w-12"><span className="sr-only">Edit</span></TableHead>
                <TableHead>Type</TableHead><TableHead className="min-w-72">Item</TableHead><TableHead>Channel / site</TableHead>
                <TableHead>Signal</TableHead><TableHead className="text-right">Conf</TableHead><TableHead>Source</TableHead>
                <TableHead className="text-right">Seen</TableHead><TableHead>Last seen</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {nodes.data.map((n) => (
                <TableRow key={n.node_id} data-state={n.node_id === edit ? "selected" : undefined}>
                  <TableCell>
                    <Button size="icon" variant="ghost" className="size-9" aria-label={`Edit ${n.label}`} onClick={() => setEdit(n.node_id)}>
                      <Pencil aria-hidden />
                    </Button>
                  </TableCell>
                  <TableCell>{n.type}</TableCell>
                  <TableCell className="min-w-72 whitespace-normal [overflow-wrap:anywhere]">{n.label}</TableCell>
                  <TableCell className="whitespace-normal [overflow-wrap:anywhere]">{n.channel || n.domain}</TableCell>
                  <TableCell>{n.signal ?? "unknown"}</TableCell>
                  <TableCell className="text-right tabular-nums">{n.confidence?.toFixed(2) ?? ""}</TableCell>
                  <TableCell>{n.source}</TableCell>
                  <TableCell className="text-right tabular-nums">{n.seen_count}</TableCell>
                  <TableCell className="whitespace-nowrap">{tz ? fmtTs(n.last_seen, tz, "short") : ""}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </div>
      )}
    </div>
  )
}

function Lookups({ tz }: { tz?: string }) {
  const rows = useKgLookups()
  if (!rows.data) return <Skeleton className="h-60 w-full" />
  if (!rows.data.length) return <Empty>No lookups yet.</Empty>
  return (
    <div className="max-h-[520px] overflow-auto rounded-lg border">
      <Table className="text-sm">
        <TableHeader className="sticky top-0 bg-background">
          <TableRow>
            <TableHead>Time</TableHead><TableHead>Outcome</TableHead><TableHead>Type</TableHead><TableHead className="min-w-72">Item</TableHead>
            <TableHead>Signal</TableHead><TableHead className="text-right">Conf</TableHead><TableHead>Detail</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {rows.data.map((r) => (
            <TableRow key={r.id}>
              <TableCell className="whitespace-nowrap tabular-nums">{tz ? fmtTs(r.ts, tz, "seconds") : ""}</TableCell>
              <TableCell>{r.outcome}</TableCell><TableCell>{r.type}</TableCell>
              <TableCell className="min-w-72 whitespace-normal [overflow-wrap:anywhere]">{r.label}</TableCell>
              <TableCell>{r.signal}</TableCell>
              <TableCell className="text-right tabular-nums">{r.confidence?.toFixed(2) ?? ""}</TableCell>
              <TableCell className="whitespace-normal [overflow-wrap:anywhere]">{r.detail}</TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </div>
  )
}
