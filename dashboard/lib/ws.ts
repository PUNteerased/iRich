/** Convert HTTP(S) telemetry base URL to WebSocket URL. */
export function telemetryWsUrl(path = '/ws/live'): string {
  const base =
    (typeof process !== 'undefined' && process.env.NEXT_PUBLIC_TELEMETRY_URL?.replace(/\/$/, '')) ||
    'http://127.0.0.1:8000'
  const wsBase = base.replace(/^http/i, (m) => (m.toLowerCase() === 'https' ? 'wss' : 'ws'))
  return `${wsBase}${path.startsWith('/') ? path : `/${path}`}`
}
