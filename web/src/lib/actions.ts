/** Saving things: run the API call(s), refresh every page's data (like the old dashboard did after each change),
 * and confirm with a short message, or show what went wrong. */
import { useState } from "react"
import { useQueryClient } from "@tanstack/react-query"
import { toast } from "sonner"

export function useAct() {
  const qc = useQueryClient()
  const [pending, setPending] = useState(0)
  async function run<T>(fn: () => Promise<T>, ok?: string | ((r: T) => string)): Promise<T | undefined> {
    setPending((n) => n + 1)
    try {
      const r = await fn()
      if (ok) toast.success(typeof ok === "function" ? ok(r) : ok)
      await qc.invalidateQueries()
      return r
    } catch (e) {
      toast.error(`Couldn't save: ${e instanceof Error ? e.message : String(e)}`)
      return undefined
    } finally {
      setPending((n) => n - 1)
    }
  }
  return { run, busy: pending > 0 }
}
