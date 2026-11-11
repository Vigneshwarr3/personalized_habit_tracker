/** Privacy & devices: pause tracking, hide date ranges (data is kept), device tokens, storage stats. */
import { useState } from "react"
import { Copy, Download, EyeOff, HardDriveDownload, Smartphone } from "lucide-react"
import { toast } from "sonner"
import { Button } from "@/components/ui/button"
import { Card } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Skeleton } from "@/components/ui/skeleton"
import { Switch } from "@/components/ui/switch"
import { Empty, Field, PageTitle, Section, SimpleSelect } from "@/components/common"
import { api } from "@/lib/api"
import { useAct } from "@/lib/actions"
import { inApp } from "@/lib/device"
import { useApk, useDevices, useHidden, useStats, useToday, type Device } from "@/lib/queries"
import { fmtTs, tsAt } from "@/lib/time"

export function Privacy() {
  const { tz } = useToday()
  return (
    <div className="max-w-4xl space-y-8">
      <PageTitle title="Privacy & devices" />
      <Pause />
      {tz && <HideRange tz={tz} />}
      {tz && <Devices tz={tz} />}
      {tz && <Storage tz={tz} />}
    </div>
  )
}

function Pause() {
  const { status } = useToday()
  const { run, busy } = useAct()
  const paused = status.data?.paused ?? false
  return (
    <Section title="Pause tracking">
      <p className="text-sm text-muted-foreground">
        Collectors check this on every upload (about once a minute) and stop recording while paused. On the Mac,{" "}
        <code className="rounded bg-muted px-1">python -m agent_mac pause</code> stops it instantly, even offline.
      </p>
      <div className="flex min-h-10 items-center gap-3">
        <Switch id="paused" checked={paused} disabled={busy || !status.data}
          onCheckedChange={(on) => run(() => api.post("/pause", { paused: on }), on ? "Tracking paused." : "Tracking resumed.")} />
        <Label htmlFor="paused">Tracking paused</Label>
      </div>
    </Section>
  )
}

function HideRange({ tz }: { tz: string }) {
  const { today } = useToday()
  const hidden = useHidden()
  const { run, busy } = useAct()
  const [d0, setD0] = useState(today ?? ""), [t0, setT0] = useState("00:00")
  const [d1, setD1] = useState(today ?? ""), [t1, setT1] = useState("23:45"), [reason, setReason] = useState("")
  const [err, setErr] = useState("")
  return (
    <Section title="Hide a date range">
      <p className="text-sm">Hidden time disappears from the dashboard and totals. <b>Nothing is deleted</b>; you can un-hide it any time.</p>
      <Card className="gap-4 px-4">
        <form className="space-y-4" onSubmit={(e) => {
          e.preventDefault()
          const a = tsAt(d0, t0, tz), b = tsAt(d1, t1, tz)
          if (b <= a) { setErr("End must be after start."); return }
          setErr("")
          run(() => api.post("/hidden-ranges", { start_ts: a, end_ts: b, reason: reason || null }), "Range hidden.")
        }}>
          <div className="grid gap-4 sm:grid-cols-4">
            <Field label="From day">{(id) => <Input id={id} type="date" className="h-10" value={d0} onChange={(e) => setD0(e.target.value)} required />}</Field>
            <Field label="From time">{(id) => <Input id={id} type="time" step={900} className="h-10" value={t0} onChange={(e) => setT0(e.target.value)} required />}</Field>
            <Field label="To day">{(id) => <Input id={id} type="date" className="h-10" value={d1} onChange={(e) => setD1(e.target.value)} required />}</Field>
            <Field label="To time">{(id) => <Input id={id} type="time" step={900} className="h-10" value={t1} onChange={(e) => setT1(e.target.value)} required />}</Field>
          </div>
          <Field label="Reason (optional)">{(id) => <Input id={id} className="h-10" value={reason} onChange={(e) => setReason(e.target.value)} />}</Field>
          {err && <p role="alert" className="text-sm text-destructive">{err}</p>}
          <Button type="submit" className="h-10" disabled={busy}><EyeOff aria-hidden /> Hide range</Button>
        </form>
      </Card>
      <ul className="divide-y rounded-lg border empty:hidden">
        {hidden.data?.filter((h) => !h.unhidden_at).map((h) => (
          <li key={h.id} className="flex items-center gap-3 px-3 py-2 text-sm">
            <span className="flex-1">{fmtTs(h.start_ts, tz)} → {fmtTs(h.end_ts, tz)}{h.reason && <> · {h.reason}</>}</span>
            <Button size="sm" variant="outline" className="h-9" disabled={busy}
              onClick={() => run(() => api.post(`/hidden-ranges/${h.id}/unhide`), "Range is visible again.")}>Un-hide</Button>
          </li>
        ))}
      </ul>
    </Section>
  )
}

function Devices({ tz }: { tz: string }) {
  const devices = useDevices()
  const apk = useApk()
  const { run, busy } = useAct()
  const [name, setName] = useState(""), [source, setSource] = useState("android")
  const [token, setToken] = useState<string | null>(null)
  return (
    <Section title="Devices">
      {inApp() ? (
        <Button variant="outline" className="h-10" asChild>
          <a href="habittracker://settings?update=1"><Smartphone aria-hidden /> Update app</a>
        </Button>
      ) : apk.data && (
        <div className="space-y-1">
          <Button variant="outline" className="h-10" asChild>
            <a href="/app/android/apk" download="habit-tracker.apk"><Download aria-hidden /> Download the Android app (.apk, {apk.data.version_name})</a>
          </Button>
          <p className="text-xs text-muted-foreground">On the phone: open this page in Chrome, download, then allow installing unknown apps for Chrome.</p>
        </div>
      )}
      {!devices.data ? <Skeleton className="h-24 w-full" /> : (
        <ul className="divide-y rounded-lg border">
          {devices.data.map((d) => <DeviceRow key={d.name} d={d} tz={tz} busy={busy}
            onRevoke={() => run(() => api.post(`/devices/${encodeURIComponent(d.name)}/revoke`), `${d.name} revoked.`)} />)}
        </ul>
      )}
      <Card className="gap-4 px-4">
        <form className="space-y-4" onSubmit={(e) => {
          e.preventDefault()
          run(() => api.post<{ token: string }>("/devices", { name, source })).then((r) => { if (r) { setToken(r.token); setName("") } })
        }}>
          <div className="grid gap-4 sm:grid-cols-2">
            <Field label="New device name" help="Letters, numbers, . _ - (names are kept forever)">
              {(id) => <Input id={id} className="h-10" value={name} placeholder="my-phone" pattern="[A-Za-z0-9_.\-]{1,64}" onChange={(e) => setName(e.target.value)} required />}
            </Field>
            <Field label="Type">
              {(id) => <SimpleSelect id={id} value={source} onChange={setSource}
                options={["android", "mac", "ios"].map((x) => ({ value: x, label: x }))} />}
            </Field>
          </div>
          <Button type="submit" className="h-10" disabled={busy || !name}>Create token</Button>
        </form>
        {token && (
          <div role="status" className="space-y-2 rounded-lg border bg-warning p-3 text-sm text-warning-foreground">
            <p><b>Copy this token into the device now.</b> It is shown only once.</p>
            <div className="flex flex-wrap items-center gap-2">
              <code className="min-w-0 flex-1 rounded bg-background px-2 py-1.5 font-mono text-xs text-foreground [overflow-wrap:anywhere]">{token}</code>
              <Button size="sm" variant="outline" className="h-9"
                onClick={() => navigator.clipboard.writeText(token).then(() => toast.success("Token copied."), () => toast.error("Couldn't copy; select it and copy by hand."))}>
                <Copy aria-hidden /> Copy
              </Button>
            </div>
          </div>
        )}
      </Card>
    </Section>
  )
}

function DeviceRow({ d, tz, busy, onRevoke }: { d: Device; tz: string; busy: boolean; onRevoke: () => void }) {
  const [confirm, setConfirm] = useState(false)
  return (
    <li className="flex flex-wrap items-center gap-3 px-3 py-2 text-sm">
      <span className="flex-1">
        <b>{d.name}</b> ({d.source}) · last upload {fmtTs(d.last_seen, tz, "short")}
        {d.revoked_at && <span className="font-medium text-destructive"> · REVOKED</span>}
      </span>
      {!d.revoked_at && (confirm ? (
        <span className="flex items-center gap-2">
          <span className="text-xs text-muted-foreground">It stops uploading for good.</span>
          <Button size="sm" variant="destructive" className="h-9" disabled={busy} onClick={onRevoke}>Revoke {d.name}</Button>
          <Button size="sm" variant="outline" className="h-9" onClick={() => setConfirm(false)}>Cancel</Button>
        </span>
      ) : (
        <Button size="sm" variant="outline" className="h-9" onClick={() => setConfirm(true)}>Revoke</Button>
      ))}
    </li>
  )
}

function Storage({ tz }: { tz: string }) {
  const stats = useStats()
  const { run, busy } = useAct()
  const [written, setWritten] = useState<string | null>(null)
  return (
    <Section title="Storage">
      {!stats.data ? <Skeleton className="h-16 w-full" /> : (
        <>
          <p className="text-sm">Database size: <b>{(stats.data.db_bytes / 1e6).toFixed(1)} MB</b>. Data is never deleted; backups run nightly and are kept.</p>
          {stats.data.devices.length ? (
            <ul className="list-disc space-y-1 pl-5 text-sm">
              {stats.data.devices.map((d) => (
                <li key={d.device}>{d.device} ({d.source}): {d.samples.toLocaleString()} samples in {d.spans.toLocaleString()} spans, {fmtTs(d.first_ts, tz, "date")} → {fmtTs(d.last_ts, tz)}</li>
              ))}
            </ul>
          ) : <Empty>No data uploaded yet.</Empty>}
        </>
      )}
      <div className="flex flex-wrap items-center gap-3">
        <Button variant="outline" className="h-10" disabled={busy}
          onClick={() => run(() => api.post<{ written: string | null }>("/backup"), "Backup done.").then((r) => r && setWritten(r.written ?? "nothing new to write"))}>
          <HardDriveDownload aria-hidden /> Back up now
        </Button>
        {written && <p className="text-sm text-muted-foreground [overflow-wrap:anywhere]">Written: {written}</p>}
      </div>
    </Section>
  )
}
