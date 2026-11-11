/** Corrections: review a day's 15-minute blocks and fix categories. Every correction is stored as a label
 * (it always wins, and it's training data for the learned model). */
import { useMemo, useState } from "react"
import { useSearchParams } from "react-router"
import { CheckCheck, ChevronDown, ExternalLink, MapPin, Search, Undo2 } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Card } from "@/components/ui/card"
import { Checkbox } from "@/components/ui/checkbox"
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible"
import { Input } from "@/components/ui/input"
import { Progress } from "@/components/ui/progress"
import { Skeleton } from "@/components/ui/skeleton"
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { CategorySelect, Empty, Field, PageTitle, Swatch } from "@/components/common"
import { api } from "@/lib/api"
import { useAct } from "@/lib/actions"
import {
  useCategories, useLabelable, useLabels, useLabelStats, usePlaces, useToday, useWindows,
  type Category, type Place,
} from "@/lib/queries"
import { addDays, fmtTime, fmtTs, localTs, tsAt } from "@/lib/time"
import { dayRows, mergeBlocks, type DayRow } from "./dayRows"

const inputCls = "h-10"

export function Corrections() {
  const [params, setParams] = useSearchParams()
  const { tz, today } = useToday()
  const day = params.get("day") ?? today
  const cats = useLabelable()
  const win = useWindows(day)
  const stats = useLabelStats()
  const { run, busy } = useAct()

  const rows = useMemo(() => (win.data && day && tz ? dayRows(day, tz, win.data.windows, win.data.locations) : undefined),
    [win.data, day, tz])
  const todo = win.data?.windows.filter((w) => w.category_id !== null && w.source !== "label").length ?? 0

  /** Save blocks as one category (back-to-back blocks become one label). */
  const apply = (blocks: [number, number][], cat: string, note?: string | null) =>
    Promise.all(mergeBlocks(blocks).map(([a, b]) => api.post("/labels", { start_ts: a, end_ts: b, category: Number(cat), note: note ?? null })))
  const catName = (id: string) => cats.data?.find((c) => String(c.id) === id)?.name ?? "category"

  const have = stats.data?.labeled_windows_approx ?? 0, need = stats.data?.needed_for_model ?? 300
  return (
    <div className="space-y-5">
      <PageTitle title="Corrections" />
      <div className="flex flex-wrap items-end gap-4">
        <Field label="Day">
          {(id) => (
            <Input id={id} type="date" className={`${inputCls} w-44`} value={day ?? ""} max={today}
              onChange={(e) => e.target.value && setParams({ day: e.target.value }, { replace: true })} />
          )}
        </Field>
        <div className="min-w-64 flex-1 space-y-1.5">
          <p className="text-sm">{have} / {need} labeled 15-min blocks for the learned model{have >= need && " ✓"}</p>
          <Progress value={Math.min(100, (100 * have) / need)} aria-label="Labeled blocks for the learned model" />
        </div>
        <div className="space-y-1">
          <Button className="h-10" disabled={todo === 0 || busy}
            onClick={() => run(() => api.post<{ confirmed_windows: number }>("/labels/confirm-day", { date: day }),
              (r) => `Confirmed ${r.confirmed_windows} blocks.`)}>
            <CheckCheck aria-hidden /> Confirm this day ({todo} blocks)
          </Button>
          <p className="max-w-72 text-xs text-muted-foreground">Fix wrong rows first. This then saves every remaining classified block of this day as correct; that's what the learned model trains on.</p>
        </div>
      </div>

      <Tabs defaultValue="table">
        <TabsList>
          <TabsTrigger value="table">Day table</TabsTrigger>
          <TabsTrigger value="fill">Quick fill</TabsTrigger>
          <TabsTrigger value="labels">Labels this day</TabsTrigger>
        </TabsList>
        <TabsContent value="table">
          {rows ? <DayTable key={day} rows={rows} cats={cats.data} busy={busy}
            onApply={(blocks, cat) => run(() => apply(blocks, cat),
              `Saved ${blocks.length} block${blocks.length > 1 ? "s" : ""} as ${catName(cat)}.`)} />
            : <Skeleton className="h-[560px] w-full" />}
        </TabsContent>
        <TabsContent value="fill">
          {rows && day && tz && <QuickFill day={day} tz={tz} rows={rows} cats={cats.data} busy={busy}
            onApply={(blocks, cat, note, ok) => run(() => apply(blocks, cat, note), ok(catName(cat)))} />}
        </TabsContent>
        <TabsContent value="labels">{day && tz && <DayLabels day={day} tz={tz} />}</TabsContent>
      </Tabs>

      {win.data && <PlacesThisDay locations={win.data.locations} cats={cats.data} />}
    </div>
  )
}

function DayTable({ rows, cats, busy, onApply }: {
  rows: DayRow[]; cats: Category[] | undefined; busy: boolean
  onApply: (blocks: [number, number][], cat: string) => Promise<unknown>
}) {
  const [picked, setPicked] = useState<Set<number>>(new Set())
  const [cat, setCat] = useState("")
  const toggle = (i: number) => setPicked((s) => { const n = new Set(s); if (n.has(i)) n.delete(i); else n.add(i); return n })
  const all = picked.size === rows.length

  return (
    <div className="space-y-2">
      <Card size="sm" className="sticky top-0 z-10 flex-row flex-wrap items-center gap-2 px-4">
        <p className="min-w-52 flex-1 text-sm">
          {picked.size ? <b>{picked.size} block{picked.size !== 1 && "s"} selected</b>
            : "Tick rows (several at once), then set their category here."}
        </p>
        <CategorySelect cats={cats} value={cat} onChange={setCat} className="w-64 flex-none" placeholder="Category to apply" />
        <Button className="h-10" disabled={!picked.size || !cat || busy}
          onClick={async () => {
            await onApply([...picked].map((i) => [rows[i].start, rows[i].end]), cat)
            setPicked(new Set())
          }}>Apply</Button>
      </Card>
      <div className="max-h-[560px] overflow-auto rounded-lg border">
        <Table className="text-sm">
          <TableHeader className="sticky top-0 z-[1] bg-background">
            <TableRow>
              <TableHead className="w-10">
                <Checkbox aria-label="Select all blocks" checked={all ? true : picked.size ? "indeterminate" : false}
                  onCheckedChange={() => setPicked(all ? new Set() : new Set(rows.map((_, i) => i)))} />
              </TableHead>
              <TableHead>Time</TableHead><TableHead>Category</TableHead><TableHead>Location</TableHead>
              <TableHead>How</TableHead><TableHead>Devices</TableHead><TableHead className="min-w-80">What (top items)</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {rows.map((r, i) => (
              <TableRow key={r.start} data-state={picked.has(i) ? "selected" : undefined} onClick={() => toggle(i)} className="cursor-pointer">
                <TableCell onClick={(e) => e.stopPropagation()}>
                  <Checkbox aria-label={`Select ${r.time}`} checked={picked.has(i)} onCheckedChange={() => toggle(i)} />
                </TableCell>
                <TableCell className="whitespace-nowrap tabular-nums">{r.time}</TableCell>
                <TableCell className="whitespace-nowrap">
                  {r.category && <span className="inline-flex items-center gap-1.5"><Swatch color={r.color ?? "#C9B6E4"} size={10} />{r.category}</span>}
                </TableCell>
                <TableCell className="whitespace-nowrap">{r.location}</TableCell>
                <TableCell className={r.empty ? "text-muted-foreground" : "whitespace-nowrap"}>{r.how}</TableCell>
                <TableCell className="whitespace-nowrap">{r.devices}</TableCell>
                <TableCell className="min-w-80 whitespace-normal text-muted-foreground [overflow-wrap:anywhere]">{r.what}</TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </div>
    </div>
  )
}

function QuickFill({ day, tz, rows, cats, busy, onApply }: {
  day: string; tz: string; rows: DayRow[]; cats: Category[] | undefined; busy: boolean
  onApply: (blocks: [number, number][], cat: string, note: string | null, ok: (name: string) => string) => Promise<unknown>
}) {
  const [cat, setCat] = useState("")
  const [t0, setT0] = useState("09:00"), [t1, setT1] = useState("10:00"), [note, setNote] = useState("")
  const empty = rows.filter((r) => r.empty).map((r) => [r.start, r.end] as [number, number])
  const off = !cat || busy
  return (
    <Card className="max-w-2xl gap-4 px-4">
      <Field label="Category">{(id) => <CategorySelect id={id} cats={cats} value={cat} onChange={setCat} />}</Field>
      <div className="flex flex-wrap gap-2">
        <Button className="h-10" disabled={off || !empty.length}
          onClick={() => onApply(empty, cat, "filled empty blocks", (n) => `Filled ${empty.length} empty blocks as ${n}.`)}>
          Fill all empty blocks ({empty.length})
        </Button>
        <Button className="h-10" variant="outline" disabled={off}
          onClick={() => onApply([[localTs(day, 0, tz), localTs(day, 8, tz)]], cat, null, (n) => `00:00–08:00 set to ${n}.`)}>
          Set 00:00–08:00
        </Button>
      </div>
      <fieldset className="space-y-3">
        <legend className="mb-2 font-medium">Or a custom range</legend>
        <div className="grid gap-3 sm:grid-cols-2">
          <Field label="From">{(id) => <Input id={id} type="time" step={900} value={t0} onChange={(e) => setT0(e.target.value)} className={inputCls} />}</Field>
          <Field label="To" help="00:00 = midnight at the end of the day">
            {(id) => <Input id={id} type="time" step={900} value={t1} onChange={(e) => setT1(e.target.value)} className={inputCls} />}
          </Field>
        </div>
        <Field label="Note (optional)">{(id) => <Input id={id} value={note} onChange={(e) => setNote(e.target.value)} className={inputCls} />}</Field>
        <Button className="h-10" variant="outline" disabled={off || !t0 || !t1}
          onClick={() => {
            const a = tsAt(day, t0, tz)
            const b = t1 <= t0 ? tsAt(addDays(day, 1), t1, tz) : tsAt(day, t1, tz)
            onApply([[a, b]], cat, note || null, (n) => `${t0}–${t1} set to ${n}.`)
          }}>Set range</Button>
      </fieldset>
      <p className="text-xs text-muted-foreground">These set the category even where something was tracked (your labels always win). Fill all empty only touches blocks with nothing in them.</p>
    </Card>
  )
}

function DayLabels({ day, tz }: { day: string; tz: string }) {
  const labels = useLabels(day)
  const cats = useCategories(true)
  const { run, busy } = useAct()
  const name = (id: number) => cats.data?.find((c) => c.id === id)?.name ?? "?"
  if (!labels.data) return <Skeleton className="h-24 w-full" />
  return (
    <div className="max-w-3xl space-y-2">
      {labels.data.length === 0 && <Empty>No labels on this day.</Empty>}
      <ul className="divide-y rounded-lg border">
        {labels.data.map((lb) => (
          <li key={lb.id} className="flex items-center gap-3 px-3 py-2 text-sm">
            <span className="flex-1">
              <span className="tabular-nums">{fmtTime(lb.start_ts, tz)}–{fmtTime(lb.end_ts, tz)}</span> · <b>{name(lb.category_id)}</b>
              {lb.note && <> · {lb.note}</>}
              <span className="text-muted-foreground"> · added {fmtTs(lb.created_at, tz, "short")}</span>
            </span>
            <Button size="sm" variant="outline" className="h-9" disabled={busy}
              onClick={() => run(() => api.post(`/labels/${lb.id}/undo`), "Label undone.")}>
              <Undo2 aria-hidden /> Undo
            </Button>
          </li>
        ))}
      </ul>
      <p className="text-xs text-muted-foreground">Undo keeps the label in history (it's deactivated, not deleted).</p>
    </div>
  )
}

function PlacesThisDay({ locations, cats }: { locations: Record<string, { label?: string; state?: string; place_id?: number }>; cats: Category[] | undefined }) {
  const ids = [...new Set(Object.values(locations)
    .filter((v) => v.place_id && ["place", "home", "work"].includes(v.state ?? "")).map((v) => v.place_id!))]
  const places = usePlaces(ids.length > 0)
  if (!ids.length) return null
  const byId = new Map(places.data?.map((p) => [p.id, p]))
  return (
    <Collapsible className="rounded-lg border">
      <CollapsibleTrigger className="group flex min-h-11 w-full items-center gap-2 px-4 text-left font-medium hover:bg-accent focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none">
        <MapPin className="size-4" aria-hidden /> Places this day ({ids.length})
        <ChevronDown className="ml-auto size-4 transition-transform group-data-[state=open]:rotate-180" aria-hidden />
      </CollapsibleTrigger>
      <CollapsibleContent className="space-y-3 px-4 pb-4">
        <p className="text-sm text-muted-foreground">Name a place and pick what you do there. Places with the same name share the tag (tag one Kroger and every Kroger counts as Shopping). Not sure where it was? Check it on OpenStreetMap.</p>
        {ids.map((id) => {
          const p = byId.get(id)
          return p ? <PlaceEditor key={`${p.id}:${p.name}:${p.category_id}:${p.osm_suggestion?.name}`} place={p} cats={cats} /> : null
        })}
      </CollapsibleContent>
    </Collapsible>
  )
}

function PlaceEditor({ place, cats }: { place: Place; cats: Category[] | undefined }) {
  const [name, setName] = useState(place.name ?? "")
  const [tag, setTag] = useState(place.category_id ? String(place.category_id) : "none")
  const { run, busy } = useAct()
  const sug = place.osm_suggestion
  return (
    <Card size="sm" className="gap-3 px-4">
      <div className="grid gap-3 sm:grid-cols-2">
        <Field label="Name">{(id) => <Input id={id} className={inputCls} value={name} placeholder={place.label} onChange={(e) => setName(e.target.value)} />}</Field>
        <Field label="Time here counts as">
          {(id) => <CategorySelect id={id} cats={cats} value={tag} onChange={setTag} allowNone="— not tagged —" />}
        </Field>
      </div>
      {sug && <p className="text-xs text-muted-foreground">OpenStreetMap: {[sug.name, sug.type, sug.address].filter(Boolean).join(" · ")}</p>}
      <div className="flex flex-wrap gap-2">
        <Button className="h-10" disabled={busy}
          onClick={() => run(() => api.patch(`/places/${place.id}`, {
            ...(name.trim() ? { name: name.trim() } : {}),
            ...(tag !== "none" ? { category_id: Number(tag) } : { clear_tag: true }),
          }), "Place saved.")}>Save</Button>
        <Button className="h-10" variant="outline" asChild>
          <a href={place.osm_url} target="_blank" rel="noopener noreferrer"><ExternalLink aria-hidden /> Open in OpenStreetMap</a>
        </Button>
        <Button className="h-10" variant="outline" disabled={busy} title="Asks OpenStreetMap what is at this spot (sends only its rounded coordinates)"
          onClick={() => run(() => api.post(`/places/${place.id}/suggest`))}>
          <Search aria-hidden /> Suggest a name
        </Button>
        {sug?.name && sug.name !== place.name && (
          <Button className="h-10" variant="outline" disabled={busy}
            onClick={() => run(() => api.patch(`/places/${place.id}`, { name: sug.name }), `Renamed to ${sug.name}.`)}>
            Use “{sug.name}”
          </Button>
        )}
      </div>
    </Card>
  )
}
