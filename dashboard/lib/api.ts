/** Telemetry client — always bypass Next.js / fetch cache (Gotcha #3). */

function isVercelHost(): boolean {
  if (typeof window === 'undefined') return false
  const h = window.location.hostname
  return h.endsWith('.vercel.app') || h === 'vercel.app'
}

/** On Vercel, call same-origin /api/bridge (server proxies to ngrok — no CORS). */
export function resolveApiBase(): string {
  if (typeof window !== 'undefined' && isVercelHost()) {
    return `${window.location.origin}/api/bridge`
  }
  if (typeof process !== 'undefined' && process.env.NEXT_PUBLIC_TELEMETRY_URL) {
    return process.env.NEXT_PUBLIC_TELEMETRY_URL.replace(/\/$/, '')
  }
  return 'http://127.0.0.1:8000'
}

export const API_BASE =
  (typeof process !== 'undefined' && process.env.NEXT_PUBLIC_TELEMETRY_URL?.replace(/\/$/, '')) ||
  'http://127.0.0.1:8000'

function telemetryHeaders(viaProxy: boolean): HeadersInit {
  const h: Record<string, string> = { Accept: 'application/json' }
  // Only needed for direct browser→ngrok; proxy adds it server-side.
  if (!viaProxy) h['ngrok-skip-browser-warning'] = '1'
  return h
}

export async function fetchTelemetry<T>(path: string, signal?: AbortSignal): Promise<T> {
  const base = resolveApiBase()
  const viaProxy = base.includes('/api/bridge')
  const url = `${base}${path.startsWith('/') ? path : `/${path}`}`
  const res = await fetch(url, {
    method: 'GET',
    cache: 'no-store',
    next: { revalidate: 0 },
    signal,
    headers: telemetryHeaders(viaProxy),
  })
  if (!res.ok) {
    throw new Error(`Telemetry ${res.status}: ${path}`)
  }
  return (await res.json()) as T
}

export async function mutateTelemetry<T>(
  path: string,
  method: 'POST' | 'PUT' | 'DELETE',
  body?: unknown,
): Promise<T> {
  const base = resolveApiBase()
  const viaProxy = base.includes('/api/bridge')
  const url = `${base}${path.startsWith('/') ? path : `/${path}`}`
  const res = await fetch(url, {
    method,
    cache: 'no-store',
    next: { revalidate: 0 },
    headers: {
      ...telemetryHeaders(viaProxy),
      ...(body !== undefined ? { 'Content-Type': 'application/json' } : {}),
    },
    body: body !== undefined ? JSON.stringify(body) : undefined,
  })
  const data = await res.json().catch(() => ({}))
  if (!res.ok) {
    const detail = (data as { detail?: string }).detail || `HTTP ${res.status}`
    throw new Error(String(detail))
  }
  return data as T
}
