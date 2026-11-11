/** Talks to the Habit Tracker API. The app is served by the API itself, so calls go to the same address:
 * no token in the browser; Tailscale's login stamp proves it's you (see server/app/auth.py). */

export class ApiError extends Error {
  readonly status: number
  readonly path: string
  constructor(status: number, path: string, detail: string) {
    super(detail)
    this.name = "ApiError"
    this.status = status
    this.path = path
  }
}

/** True when the server couldn't be reached at all (offline, Tailscale disconnected, server down). */
export function isNetworkError(e: unknown): boolean {
  return e instanceof TypeError || (e instanceof ApiError && [502, 503, 504].includes(e.status))
}

type Params = Record<string, string | number | boolean | null | undefined>

function url(path: string, params?: Params): string {
  const q = new URLSearchParams()
  for (const [k, v] of Object.entries(params ?? {})) if (v !== null && v !== undefined) q.set(k, String(v))
  const s = q.toString()
  return s ? `${path}?${s}` : path
}

async function request<T>(method: string, path: string, params?: Params, body?: unknown): Promise<T> {
  const r = await fetch(url(path, params), {
    method,
    credentials: "same-origin",
    headers: body === undefined ? undefined : { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  })
  if (!r.ok) {
    let detail = r.statusText || `HTTP ${r.status}`
    try {
      const j = await r.json()
      if (j?.detail) detail = typeof j.detail === "string" ? j.detail : JSON.stringify(j.detail)
    } catch {
      /* not JSON */
    }
    throw new ApiError(r.status, path, detail)
  }
  return (await r.json()) as T
}

export const api = {
  get: <T>(path: string, params?: Params) => request<T>("GET", path, params),
  post: <T>(path: string, body?: unknown) => request<T>("POST", path, undefined, body ?? {}),
  put: <T>(path: string, body?: unknown) => request<T>("PUT", path, undefined, body ?? {}),
  patch: <T>(path: string, body?: unknown) => request<T>("PATCH", path, undefined, body ?? {}),
}
