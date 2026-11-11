/** Summary cards (which ones show is chosen in Settings). */
import { ArrowDown, ArrowUp } from "lucide-react"
import { Card } from "@/components/ui/card"
import { Metric } from "@/components/common"
import { cn } from "@/lib/utils"
import type { Kpis } from "@/lib/queries"
import { fmtDate, fmtHours, fmtSigned, fmtTime } from "@/lib/time"

type Row = { color: string | null; name: string; hours: number; prev: number }

function ListCard({ title, rows, compare }: { title: string; rows: Row[]; compare: boolean }) {
  return (
    <Card size="sm" className="w-full gap-2 px-4 md:w-[340px]">
      <h3 className="font-semibold">
        {title}{compare && <span className="font-normal text-muted-foreground text-xs"> · vs previous</span>}
      </h3>
      {rows.length === 0 ? (
        <p className="text-muted-foreground">No data yet</p>
      ) : (
        <ul className="space-y-1.5">
          {rows.map((r) => (
            <li key={r.name} className="flex items-center gap-2">
              {r.color && <span aria-hidden className="size-2.5 flex-none rounded-[3px]" style={{ background: r.color }} />}
              <span className="min-w-0 flex-1 [overflow-wrap:anywhere]">{r.name}</span>
              <b className="font-semibold whitespace-nowrap tabular-nums">{fmtHours(r.hours)} h</b>
              {compare && (
                <span className="min-w-14 text-right text-xs text-muted-foreground tabular-nums">
                  {fmtSigned(r.hours - r.prev)}
                </span>
              )}
            </li>
          ))}
        </ul>
      )}
    </Card>
  )
}

/** good = up is good, bad = up is bad (e.g. time to cut), off = neutral */
type Tone = "good" | "bad" | "off"

function Delta({ text, value, tone, arrow = true }: { text: string; value: number; tone: Tone; arrow?: boolean }) {
  const up = value > 0
  const color = tone === "off" || value === 0 ? "text-muted-foreground"
    : (up === (tone === "good")) ? "text-good" : "text-bad"
  const Icon = up ? ArrowUp : ArrowDown
  return (
    <span className={cn("inline-flex items-center gap-0.5 text-xs font-medium", color)}>
      {arrow && value !== 0 && <Icon className="size-3" aria-hidden />}{text}
    </span>
  )
}

function hoursDelta(cur: number, prev: number, tone: Tone) {
  const d = cur - prev
  return Math.abs(d) < 0.05 ? undefined : <Delta text={`${fmtSigned(d)} h`} value={d} tone={tone} />
}

export function Cards({ k, tz }: { k: Kpis; tz: string }) {
  const show = (key: string) => k.cards[key] ?? true
  const cmp = show("change_vs_previous")
  const f = k.focus
  const s = k.streak
  const cov = k.coverage
  const pl = k.phone_vs_laptop

  return (
    <div className="space-y-2">
      <div className="flex flex-wrap gap-2">
        {show("top3") && (
          <ListCard title="Top 3" compare={cmp}
            rows={k.top3.map((t) => ({ color: t.color, name: t.short_name, hours: t.hours, prev: t.prev_hours }))} />
        )}
        {show("group_totals") && (
          <ListCard title="Activities" compare={cmp}
            rows={[...k.group_totals].sort((a, b) => b.hours - a.hours)
              .map((g) => ({ color: g.color, name: g.group, hours: g.hours, prev: g.prev_hours }))} />
        )}
        {show("phone_vs_laptop") && (
          <ListCard title="Phone vs laptop" compare={false} rows={[
            { color: null, name: "Phone", hours: Math.round(pl.phone_hours * 10) / 10, prev: 0 },
            { color: null, name: "Laptop", hours: Math.round(pl.laptop_hours * 10) / 10, prev: 0 },
          ]} />
        )}
      </div>
      <div className="flex flex-wrap gap-2">
        {show("time_to_cut") && k.time_to_cut && (
          <Metric label="Time to cut" value={`${fmtHours(k.time_to_cut.hours)} h`}
            help={`Group: ${k.time_to_cut.group} (change in Settings)`}
            sub={cmp ? hoursDelta(k.time_to_cut.hours, k.time_to_cut.prev_hours, "bad") : undefined} />
        )}
        {show("coverage") && (
          <Metric label="Day coverage" value={cov.percent !== null ? `${Math.round(cov.percent)}%` : "—"}
            help={`${cov.filled_blocks} of ${cov.total_blocks} 15-min blocks so far have a category`} />
        )}
        {show("commute") && (
          <Metric label="Commute time" value={`${fmtHours(k.commute.hours)} h`}
            help="All time on the move near home, including when you were also doing something on a device"
            sub={cmp ? hoursDelta(k.commute.hours, k.commute.prev_hours, "off") : undefined} />
        )}
        {show("focus") && (
          <Metric label="Longest focus" value={`${fmtHours(f.hours)} h`}
            help={`Longest unbroken run of focus categories${f.date ? ` (${fmtDate(f.date, { month: "short", day: "2-digit" })})` : ""}. Choose them in Settings.`}
            sub={f.start_ts && f.end_ts && f.date ? (
              <span className="text-xs text-muted-foreground">
                {fmtDate(f.date, { weekday: "short" })} {fmtTime(f.start_ts, tz)}–{fmtTime(f.end_ts, tz)}
              </span>
            ) : undefined} />
        )}
        {show("streak") && (
          <Metric label="Streak" value={`${s.current_days} days`}
            help={`Days in a row with ${s.rule}.${s.today_pending ? " Today doesn't count yet." : ""}`}
            sub={<span className="text-xs text-muted-foreground">best {s.best_days}</span>} />
        )}
      </div>
    </div>
  )
}
