/** Activities and sub-activities: colors (sub-activities follow their activity's color unless you pick one),
 * renames, new ones, archive, and moving old categories into a sub-activity. Nothing is deleted. */
import { useState } from "react"
import { Undo2 } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Card } from "@/components/ui/card"
import { Checkbox } from "@/components/ui/checkbox"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Skeleton } from "@/components/ui/skeleton"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { CategorySelect, Empty, Field, PageTitle, SimpleSelect, Swatch } from "@/components/common"
import { api } from "@/lib/api"
import { useAct } from "@/lib/actions"
import { useCategories, type Category } from "@/lib/queries"

const byOrder = (a: Category, b: Category) => a.sort_order - b.sort_order || a.id - b.id

function ColorField({ label, value, onChange }: { label: string; value: string; onChange: (v: string) => void }) {
  return (
    <Field label={label}>
      {(id) => (
        <div className="flex items-center gap-2">
          <input id={id} type="color" value={value} onChange={(e) => onChange(e.target.value)}
            className="h-10 w-14 cursor-pointer rounded-md border border-input bg-background p-1" />
          <span className="font-mono text-sm text-muted-foreground uppercase">{value}</span>
        </div>
      )}
    </Field>
  )
}

export function Categories() {
  const cats = useCategories(true)
  if (!cats.data) return <><PageTitle title="Activities" /><Skeleton className="h-96 w-full" /></>
  const all = cats.data
  const live = all.filter((c) => !c.merged_into)
  const activities = live.filter((c) => c.is_activity).sort(byOrder)
  const subsOf = new Map(activities.map((a) => [a.id, live.filter((c) => c.parent_id === a.id).sort(byOrder)]))
  const old = live.filter((c) => !c.is_activity && c.parent_id === null && !c.archived)

  return (
    <div className="space-y-6">
      <PageTitle title="Activities" />
      <ul className="flex flex-wrap gap-2">
        {activities.map((a) => {
          const subs = (subsOf.get(a.id) ?? []).filter((s) => !s.archived)
          return (
            <li key={a.id} className="w-full sm:w-[250px]">
              <Card size="sm" className="h-full gap-2 px-4">
                <h3 className="flex items-center gap-2 font-semibold"><Swatch color={a.color} size={16} />{a.name}</h3>
                {subs.length ? (
                  <ul className="space-y-1 text-sm">
                    {subs.map((s) => <li key={s.id} className="flex items-center gap-2"><Swatch color={s.color} size={11} />{s.short_name}</li>)}
                  </ul>
                ) : <p className="text-sm text-muted-foreground">no sub-activities</p>}
              </Card>
            </li>
          )
        })}
      </ul>

      <Tabs defaultValue="act">
        <TabsList className="h-auto flex-wrap">
          <TabsTrigger value="act">Edit an activity</TabsTrigger>
          <TabsTrigger value="sub">Edit a sub-activity</TabsTrigger>
          <TabsTrigger value="new">New activity</TabsTrigger>
          <TabsTrigger value="move">Move old categories ({old.length})</TabsTrigger>
        </TabsList>
        <TabsContent value="act"><EditActivity activities={activities} /></TabsContent>
        <TabsContent value="sub"><EditSub activities={activities} subsOf={subsOf} /></TabsContent>
        <TabsContent value="new"><NewActivity /></TabsContent>
        <TabsContent value="move"><MoveOld all={all} old={old} activities={activities} subsOf={subsOf} /></TabsContent>
      </Tabs>
    </div>
  )
}

function EditActivity({ activities }: { activities: Category[] }) {
  const [pick, setPick] = useState(activities[0] ? String(activities[0].id) : "")
  const a = activities.find((x) => String(x.id) === pick)
  return (
    <Card className="max-w-xl gap-4 px-4">
      <Field label="Activity">{(id) => <CategorySelect id={id} cats={activities} value={pick} onChange={setPick} />}</Field>
      {a && <ActivityForm key={`${a.id}:${a.name}:${a.color}`} a={a} />}
    </Card>
  )
}

function ActivityForm({ a }: { a: Category }) {
  const [name, setName] = useState(a.name), [color, setColor] = useState(a.color), [sub, setSub] = useState("")
  const { run, busy } = useAct()
  return (
    <form className="space-y-4" onSubmit={(e) => {
      e.preventDefault()
      run(async () => {
        const patch: Record<string, string> = {}
        if (name.trim() !== a.name) patch.name = name.trim()
        if (color.toUpperCase() !== a.color.toUpperCase()) patch.color = color
        if (Object.keys(patch).length) await api.patch(`/categories/${a.id}`, patch)
        if (sub.trim()) await api.post(`/categories/${a.id}/subs`, { name: sub.trim() })
        setSub("")
      }, "Saved.")
    }}>
      <Field label="Name">{(id) => <Input id={id} className="h-10" value={name} onChange={(e) => setName(e.target.value)} required />}</Field>
      <ColorField label="Color (sub-activities follow it unless you gave them their own)" value={color} onChange={setColor} />
      <Field label="Add a sub-activity (optional)">{(id) => <Input id={id} className="h-10" value={sub} placeholder="e.g. Podcasts" onChange={(e) => setSub(e.target.value)} />}</Field>
      <Button type="submit" className="h-10" disabled={busy}>Save</Button>
    </form>
  )
}

function EditSub({ activities, subsOf }: { activities: Category[]; subsOf: Map<number, Category[]> }) {
  const pairs = activities.flatMap((a) => (subsOf.get(a.id) ?? []).map((s) => ({ a, s })))
  const [pick, setPick] = useState(pairs[0] ? String(pairs[0].s.id) : "")
  const sel = pairs.find((p) => String(p.s.id) === pick)
  return (
    <Card className="max-w-xl gap-4 px-4">
      <Field label="Sub-activity">
        {(id) => <SimpleSelect id={id} value={pick} onChange={setPick}
          options={pairs.map(({ a, s }) => ({ value: String(s.id), label: `${a.name} › ${s.short_name}${s.archived ? " (archived)" : ""}` }))} />}
      </Field>
      {sel && <SubForm key={`${sel.s.id}:${sel.s.name}:${sel.s.color}:${sel.s.archived}`} s={sel.s} />}
    </Card>
  )
}

function SubForm({ s }: { s: Category }) {
  const [name, setName] = useState(s.short_name), [color, setColor] = useState(s.color), [archived, setArchived] = useState(s.archived)
  const { run, busy } = useAct()
  return (
    <form className="space-y-4" onSubmit={(e) => {
      e.preventDefault()
      const patch: Record<string, string | boolean> = {}
      if (name.trim() !== s.short_name) patch.name = name.trim()
      if (color.toUpperCase() !== s.color.toUpperCase()) patch.color = color
      if (archived !== s.archived) patch.archived = archived
      run(() => (Object.keys(patch).length ? api.patch(`/categories/${s.id}`, patch) : Promise.resolve()), "Saved.")
    }}>
      <Field label="Name">{(id) => <Input id={id} className="h-10" value={name} onChange={(e) => setName(e.target.value)} required />}</Field>
      <ColorField label="Color" value={color} onChange={setColor} />
      <div className="flex min-h-10 items-center gap-2">
        <Checkbox id={`arch-${s.id}`} checked={archived} onCheckedChange={(v) => setArchived(v === true)} />
        <Label htmlFor={`arch-${s.id}`}>Archived (hidden from pickers; history kept)</Label>
      </div>
      <div className="flex flex-wrap gap-2">
        <Button type="submit" className="h-10" disabled={busy}>Save</Button>
        {s.color_custom && (
          <Button type="button" variant="outline" className="h-10" disabled={busy}
            onClick={() => run(() => api.post(`/categories/${s.id}/follow-activity-color`), "Now follows the activity color.")}>
            Use a shade of the activity color again
          </Button>
        )}
      </div>
    </form>
  )
}

function NewActivity() {
  const [name, setName] = useState(""), [color, setColor] = useState("#7F8C99"), [subs, setSubs] = useState("")
  const { run, busy } = useAct()
  return (
    <Card className="max-w-xl gap-4 px-4">
      <form className="space-y-4" onSubmit={(e) => {
        e.preventDefault()
        if (!name.trim()) return
        run(async () => {
          const created = await api.post<{ id: number }>("/categories/activities", { name: name.trim(), color })
          for (const sub of subs.split(",").map((x) => x.trim()).filter(Boolean)) await api.post(`/categories/${created.id}/subs`, { name: sub })
          setName(""); setSubs("")
        }, "Activity added.")
      }}>
        <Field label="Activity name">{(id) => <Input id={id} className="h-10" value={name} onChange={(e) => setName(e.target.value)} required />}</Field>
        <ColorField label="Color" value={color} onChange={setColor} />
        <Field label="Sub-activities (comma-separated, optional)">
          {(id) => <Input id={id} className="h-10" value={subs} placeholder="e.g. Guitar, Singing" onChange={(e) => setSubs(e.target.value)} />}
        </Field>
        <Button type="submit" className="h-10" disabled={busy || !name.trim()}>Add activity</Button>
      </form>
    </Card>
  )
}

function MoveOld({ all, old, activities, subsOf }: { all: Category[]; old: Category[]; activities: Category[]; subsOf: Map<number, Category[]> }) {
  const { run, busy } = useAct()
  const targets = [
    ...activities.flatMap((a) => (subsOf.get(a.id) ?? []).filter((s) => !s.archived)),
    ...activities.filter((a) => !(subsOf.get(a.id) ?? []).length),
  ]
  const [choice, setChoice] = useState<Record<number, string>>({})
  const moved = all.filter((c) => c.merged_into && !c.is_activity && c.parent_id === null && !c.archived)
  const nameOf = (id: number | null) => all.find((c) => c.id === id)?.name ?? "?"
  return (
    <div className="max-w-3xl space-y-3">
      <p className="text-sm text-muted-foreground">These are your categories from before activities. Move each into a sub-activity when you're ready: everything recorded under it then shows as that sub-activity. It's reversible (undo below) and nothing is rewritten.</p>
      {!old.length && <Empty>All old categories have been moved.</Empty>}
      <ul className="space-y-2">
        {old.map((c) => {
          const tgt = choice[c.id] ?? ""
          return (
            <li key={c.id} className="flex flex-wrap items-center gap-2 rounded-lg border p-2">
              <span className="flex min-w-48 flex-1 items-center gap-2 text-sm"><Swatch color={c.color} />{c.name}</span>
              <CategorySelect cats={targets} value={tgt} onChange={(v) => setChoice((s) => ({ ...s, [c.id]: v }))}
                placeholder={`Move ${c.name} into…`} className="w-64" />
              <Button className="h-10" disabled={!tgt || busy}
                onClick={() => run(() => api.post("/categories/merge", { source: c.id, target: Number(tgt) }),
                  `Moved ${c.name} into ${nameOf(Number(tgt))}.`)}>Move</Button>
            </li>
          )
        })}
      </ul>
      {moved.length > 0 && (
        <details className="rounded-lg border">
          <summary className="flex min-h-11 cursor-pointer items-center px-4 font-medium">Already moved ({moved.length})</summary>
          <ul className="divide-y border-t">
            {moved.map((c) => (
              <li key={c.id} className="flex items-center gap-3 px-4 py-2 text-sm">
                <span className="flex-1">{c.name} → {nameOf(c.merged_into)}</span>
                <Button size="sm" variant="outline" className="h-9" disabled={busy}
                  onClick={() => run(() => api.post(`/categories/${c.id}/unmerge`), `Moved ${c.name} back.`)}>
                  <Undo2 aria-hidden /> Undo
                </Button>
              </li>
            ))}
          </ul>
        </details>
      )}
    </div>
  )
}
