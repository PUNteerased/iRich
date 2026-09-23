/** Display timestamps in Thailand time only (Asia/Bangkok / ICT). */

export const DISPLAY_TZ = 'Asia/Bangkok'

const timeFmt = new Intl.DateTimeFormat('en-GB', {
  timeZone: DISPLAY_TZ,
  hour: '2-digit',
  minute: '2-digit',
  second: '2-digit',
  hour12: false,
})

const dateTimeFmt = new Intl.DateTimeFormat('en-GB', {
  timeZone: DISPLAY_TZ,
  year: 'numeric',
  month: 'short',
  day: '2-digit',
  hour: '2-digit',
  minute: '2-digit',
  second: '2-digit',
  hour12: false,
})

export function formatIctTime(iso: string | null | undefined): string {
  if (!iso) return '—'
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return '—'
  return `${timeFmt.format(d)} ICT`
}

export function formatIctDateTime(iso: string | null | undefined): string {
  if (!iso) return '—'
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return '—'
  return `${dateTimeFmt.format(d)} ICT`
}

export function nowIctLabel(): string {
  return `${timeFmt.format(new Date())} ICT`
}
