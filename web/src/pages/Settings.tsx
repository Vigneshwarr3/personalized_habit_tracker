/** Settings: which Overview cards to show, the rules behind them, session thresholds and location.
 * Saved on the server, so the Mac and the phone look the same. Activities themselves are edited on Categories. */
import { useState } from "react"
import { Button } from "@/components/ui/button"
import { Card } from "@/components/ui/card"
import { Checkbox } from "@/components/ui/checkbox"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Skeleton } from "@/components/ui/skeleton"
import { Switch } from "@/components/ui/switch"
import { CategorySelect, Field, PageTitle, Section, SimpleSelect } from "@/components/common"
import { api } from "@/lib/api"
import { useAct } from "@/lib/actions"
import { useCategories, useLabelable, useUiSettings, type Category, type UiSettings } from "@/lib/queries"

export function Settings() {
  const ui = useUiSettings()
  const cats = useCategories(true)
  const labelable = useLabelable()
  if (!ui.data || !cats.data || !labelable.data) return <><PageTitle title="Settings" /><Skeleton className="h-96 w-full" /></>
  const acts = cats.data.filter((c) => c.is_activity && !c.merged_into && !c.archived)
    .sort((a, b) => a.sort_order - b.sort_order || a.id - b.id)
  // re-create the form when the saved settings change (after Save, or when fresher data arrives)
  return <SettingsForm key={JSON.stringify(ui.data)} ui={ui.data} acts={acts} labelable={labelable.data} />
}

/** Number box with limits. step="any": saved values needn't line up with a step (the browser would refuse to submit). */
function Num({ label, value, onChange, min, max, help }: {
  label: string; value: number; onChange: (n: number) => void; min: number; max: number; help?: string
}) {
  return (
    <Field label={label} help={help}>
      {(id) => <Input id={id} type="number" inputMode="decimal" className="h-10" min={min} max={max} step="any" value={value}
        onChange={(e) => onChange(e.target.value === "" ? min : Number(e.target.value))} />}
    </Field>
  )
}

function Checks({ label, items, value, onChange, filterable }: {
  label: string; items: Category[]; value: number[]; onChange: (v: number[]) => void; filterable?: boolean
}) {
  const [q, setQ] = useState("")
  const shown = items.filter((c) => c.name.toLowerCase().includes(q.toLowerCase()))
  const set = new Set(value)
  return (
    <fieldset className="space-y-2">
      <legend className="text-sm font-medium">{label} <span className="font-normal text-muted-foreground">({value.length} chosen)</span></legend>
      {filterable && <Input aria-label={`Filter ${label}`} placeholder="Filter…" className="h-10 max-w-sm" value={q} onChange={(e) => setQ(e.target.value)} />}
      <div className={`grid gap-x-4 gap-y-1 sm:grid-cols-2 lg:grid-cols-3 ${filterable ? "max-h-64 overflow-auto rounded-md border p-2" : ""}`}>
        {shown.map((c) => (
          <label key={c.id} className="flex min-h-9 cursor-pointer items-center gap-2 text-sm">
            <Checkbox checked={set.has(c.id)}
              onCheckedChange={(on) => onChange(on ? [...value, c.id] : value.filter((x) => x !== c.id))} />
            {c.name}
          </label>
        ))}
      </div>
    </fieldset>
  )
}

function SettingsForm({ ui, acts, labelable }: { ui: UiSettings; acts: Category[]; labelable: Category[] }) {
  const [v, setV] = useState(ui)
  const { run, busy } = useAct()
  const actIds = new Set(acts.map((a) => a.id)), labelIds = new Set(labelable.map((c) => c.id))
  const up = (patch: Partial<UiSettings>) => setV((s) => ({ ...s, ...patch }))

  return (
    <form className="space-y-8" onSubmit={(e) => {
      e.preventDefault()
      const body = {
        cards: v.cards, time_to_cut_activity: v.time_to_cut_activity, show_legends: v.show_legends,
        streak: v.streak, sessions: v.sessions, location: v.location,
        focus: { activities: v.focus.activities.filter((a) => actIds.has(a)), categories: v.focus.categories.filter((c) => labelIds.has(c)) },
      }
      run(() => api.put("/ui-settings", body), "Settings saved.")
    }}>
      <PageTitle title="Settings" />

      <Section title="Overview cards">
        <p className="text-sm text-muted-foreground">Show or hide each card on the Overview page.</p>
        <div className="flex flex-wrap gap-x-6 gap-y-3">
          {Object.entries(ui.card_labels).map(([k, label]) => (
            <label key={k} className="flex min-h-9 cursor-pointer items-center gap-2 text-sm">
              <Switch checked={v.cards[k] ?? true} onCheckedChange={(on) => up({ cards: { ...v.cards, [k]: on } })} />
              {label}
            </label>
          ))}
        </div>
      </Section>

      <Section title="Overview charts">
        <div className="flex min-h-10 items-center gap-2">
          <Switch id="legends" checked={v.show_legends} onCheckedChange={(on) => up({ show_legends: on })} />
          <Label htmlFor="legends">Show color legends under the heatmap and the pie chart</Label>
        </div>
      </Section>

      <Section title="Card rules">
        <Card className="max-w-3xl gap-4 px-4">
          <Field label="Time to cut = this activity">
            {(id) => <CategorySelect id={id} cats={acts} value={v.time_to_cut_activity ? String(v.time_to_cut_activity) : ""}
              onChange={(x) => up({ time_to_cut_activity: Number(x) })} />}
          </Field>
          <div className="grid gap-4 sm:grid-cols-2">
            <Field label="Streak: activity">
              {(id) => <CategorySelect id={id} cats={acts} value={v.streak.activity ? String(v.streak.activity) : ""}
                onChange={(x) => up({ streak: { ...v.streak, activity: Number(x) } })} />}
            </Field>
            <Num label="Streak: at least (hours per day)" min={0} max={24} value={v.streak.min_hours}
              onChange={(n) => up({ streak: { ...v.streak, min_hours: n } })} />
          </div>
          <Checks label="Focus counts these activities" items={acts} value={v.focus.activities.filter((a) => actIds.has(a))}
            onChange={(x) => up({ focus: { ...v.focus, activities: x } })} />
          <Checks label="…and these sub-activities" items={labelable} filterable value={v.focus.categories.filter((c) => labelIds.has(c))}
            onChange={(x) => up({ focus: { ...v.focus, categories: x } })} />
        </Card>
      </Section>

      <Section title="Sessions">
        <p className="text-sm text-muted-foreground">A whole session counts once it passes the threshold. Blocks you labeled yourself are never changed.</p>
        <div className="grid max-w-3xl gap-4 sm:grid-cols-2">
          <Num label="Binge watching: watching for at least (hours)" min={0.25} max={24} value={v.sessions.binge_hours}
            onChange={(n) => up({ sessions: { ...v.sessions, binge_hours: n } })} />
          <Num label="Doom scrolling: social media / short videos for at least (minutes)" min={5} max={600} value={v.sessions.doom_minutes}
            onChange={(n) => up({ sessions: { ...v.sessions, doom_minutes: n } })} />
        </div>
      </Section>

      <Section title="Location (phone)">
        <div className="grid max-w-3xl gap-4 sm:grid-cols-3">
          <Num label="Travel = farther from home than (km)" min={1} max={5000} value={v.location.travel_km}
            onChange={(n) => up({ location: { ...v.location, travel_km: n } })} />
          <Field label="Coordinate precision" help="Applies to new data only: stored coordinates can't be made more precise later.">
            {(id) => <SimpleSelect id={id} value={v.location.precision}
              onChange={(x) => up({ location: { ...v.location, precision: x as "100m" | "1km" } })}
              options={[{ value: "100m", label: "about 100 m" }, { value: "1km", label: "about 1 km" }]} />}
          </Field>
          <Num label="Place radius (m)" min={50} max={2000} value={v.location.radius_m} help="How close counts as being at Home, Work or another place"
            onChange={(n) => up({ location: { ...v.location, radius_m: n } })} />
        </div>
        <div className="flex min-h-10 items-center gap-2">
          <Switch id="autofill" checked={v.location.autofill_places}
            onCheckedChange={(on) => up({ location: { ...v.location, autofill_places: on } })} />
          <Label htmlFor="autofill">Fill empty blocks at places you've labeled the same way twice</Label>
        </div>
      </Section>

      <div className="sticky bottom-0 -mx-4 flex flex-wrap items-center gap-3 border-t bg-background/95 px-4 py-3 backdrop-blur sm:-mx-6 sm:px-6">
        <Button type="submit" className="h-10" disabled={busy}>Save settings</Button>
        <p className="text-xs text-muted-foreground">Activities, sub-activities and their colors are edited on the Categories page.</p>
      </div>
    </form>
  )
}
