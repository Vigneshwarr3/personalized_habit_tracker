/** Small building blocks shared by the pages. */
import { useId, type ReactNode } from "react"
import { CircleHelp } from "lucide-react"
import { Card } from "@/components/ui/card"
import { Label } from "@/components/ui/label"
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { cn } from "@/lib/utils"

export function PageTitle({ title, children }: { title: string; children?: ReactNode }) {
  return (
    <div className="mb-5 space-y-1">
      <h1 className="text-2xl font-semibold">{title}</h1>
      {children && <p className="max-w-3xl text-sm text-muted-foreground">{children}</p>}
    </div>
  )
}

export function Section({ title, children, className }: { title: string; children: ReactNode; className?: string }) {
  return (
    <section className={cn("space-y-3", className)}>
      <h2 className="text-lg font-semibold">{title}</h2>
      {children}
    </section>
  )
}

/** A labelled control; `help` sits under it. The control gets the id passed to the render function. */
export function Field({ label, help, children, className }: {
  label: string; help?: ReactNode; className?: string; children: (id: string) => ReactNode
}) {
  const id = useId()
  return (
    <div className={cn("grid content-start gap-1.5", className)}>
      <Label htmlFor={id}>{label}</Label>
      {children(id)}
      {help && <p className="text-xs text-muted-foreground">{help}</p>}
    </div>
  )
}

export function Swatch({ color, size = 12 }: { color: string; size?: number }) {
  return (
    <span aria-hidden className="inline-block flex-none rounded-[4px] align-middle shadow-[inset_0_0_0_1px_rgba(128,128,128,.3)]"
      style={{ background: color, width: size, height: size }} />
  )
}

export function Help({ label, children }: { label: string; children: ReactNode }) {
  return (
    <Popover>
      <PopoverTrigger
        aria-label={`About ${label}`}
        className="-m-2 inline-flex size-8 items-center justify-center rounded-md text-muted-foreground hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none"
      >
        <CircleHelp className="size-3.5" aria-hidden />
      </PopoverTrigger>
      <PopoverContent className="w-64 text-sm">{children}</PopoverContent>
    </Popover>
  )
}

/** A number card (label, big value, small line under it). */
export function Metric({ label, value, help, sub }: { label: string; value: ReactNode; help?: ReactNode; sub?: ReactNode }) {
  return (
    <Card size="sm" className="w-[calc(50%-0.25rem)] gap-1 px-4 sm:w-[170px]">
      <div className="flex items-center justify-between gap-1">
        <h3 className="text-sm text-muted-foreground">{label}</h3>
        {help && <Help label={label}>{help}</Help>}
      </div>
      <p className="text-2xl font-semibold tabular-nums">{value}</p>
      <div className="min-h-4 text-xs text-muted-foreground">{sub}</div>
    </Card>
  )
}

/** Pick a category (with its color). `value` is the category id as a string ("" = none chosen). */
export function CategorySelect({ cats, value, onChange, id, placeholder = "Choose a category", className, allowNone }: {
  cats: { id: number; name: string; color: string }[] | undefined
  value: string; onChange: (v: string) => void; id?: string; placeholder?: string; className?: string
  allowNone?: string // label for a "none" option (value "none")
}) {
  return (
    <Select value={value} onValueChange={onChange}>
      <SelectTrigger id={id} className={cn("h-10 w-full", className)} aria-label={id ? undefined : placeholder}>
        <SelectValue placeholder={cats ? placeholder : "Loading…"} />
      </SelectTrigger>
      <SelectContent>
        {allowNone && <SelectItem value="none">{allowNone}</SelectItem>}
        {cats?.map((c) => (
          <SelectItem key={c.id} value={String(c.id)}>
            <Swatch color={c.color} size={10} />{c.name}
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  )
}

/** Plain select over string options. */
export function SimpleSelect({ value, onChange, options, id, className, ariaLabel }: {
  value: string; onChange: (v: string) => void; options: { value: string; label: string }[]
  id?: string; className?: string; ariaLabel?: string
}) {
  return (
    <Select value={value} onValueChange={onChange}>
      <SelectTrigger id={id} className={cn("h-10 w-full", className)} aria-label={ariaLabel}><SelectValue /></SelectTrigger>
      <SelectContent>
        {options.map((o) => <SelectItem key={o.value} value={o.value}>{o.label}</SelectItem>)}
      </SelectContent>
    </Select>
  )
}

/** Shown when a list is empty. */
export function Empty({ children }: { children: ReactNode }) {
  return <p className="rounded-lg border bg-muted px-4 py-3 text-sm">{children}</p>
}

