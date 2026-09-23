/** Telemetry client — always bypass Next.js / fetch cache (Gotcha #3). */

export const API_BASE =
  process.env.NEXT_PUBLIC_TELEMETRY_URL?.replace(/\/$/, '') || 'http://127.0.0.1:8000'

export async function fetchTelemetry<T>(path: string, signal?: AbortSignal): Promise<T> {
  const url = `${API_BASE}${path.startsWith('/') ? path : `/${path}`}`
  const res = await fetch(url, {
    method: 'GET',
    cache: 'no-store',
    next: { revalidate: 0 },
    signal,
    headers: { Accept: 'application/json' },
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
  const url = `${API_BASE}${path.startsWith('/') ? path : `/${path}`}`
  const res = await fetch(url, {
    method,
    cache: 'no-store',
    next: { revalidate: 0 },
    headers: {
      Accept: 'application/json',
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
