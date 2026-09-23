'use client'

import { useMemo, useState } from 'react'
import { CalendarDays, ChevronLeft, ChevronRight } from 'lucide-react'
import { useTelemetry } from '@/lib/useTelemetry'
import { formatIctTime } from '@/lib/time'

type DayCell = {
  date: string | null
  in_month?: boolean
  day?: number
  pnl_usd?: number
  trades?: number
  r_multiple?: number
  wins?: number
  losses?: number
}

type WeekRow = {
  week_index: number
  label: string
  start: string
  end: string
  pnl_usd: number
  trades: number
  days_traded: number
  empty?: boolean
}

type CalendarPayload = {
  year: number
  month: number
  month_label: string
  view: string
  weekday_labels: string[]
  days: DayCell[]
  weeks: WeekRow[]
  year_weeks: WeekRow[]
  summary: {
    net_pnl_usd: number
    trades: number
    wins: number
    losses: number
    win_rate: number
    total_r: number
    avg_win_usd: number
    avg_loss_usd: number
    profit_factor: number | null
    expected_value_usd: number
  }
  selected_day_trades?: {
    day: string
    ts: string
    symbol?: string
    pnl_usd: number
    r_multiple: number
    outcome?: string
  }[]
}

function money(n: number | null | undefined, digits = 2): string {
  if (n == null || Number.isNaN(n)) return '$0.00'
  const sign = n > 0 ? '+' : n < 0 ? '' : ''
  return `${sign}$${n.toFixed(digits)}`
}

function cellClass(pnl: number, trades: number): string {
  if (!trades) return 'perf-cell flat'
  if (pnl > 0) return pnl >= 2 ? 'perf-cell profit' : 'perf-cell profit low'
  if (pnl < 0) return 'perf-cell loss'
  return 'perf-cell flat'
}

export function PerformanceCalendar() {
  const now = new Date()
  const [year, setYear] = useState(now.getFullYear())
  const [month, setMonth] = useState(now.getMonth() + 1)
  const [view, setView] = useState<'month' | 'week' | 'year'>('month')
  const [selectedDay, setSelectedDay] = useState<string | null>(null)

  const path = useMemo(() => {
    const q = new URLSearchParams({
      year: String(year),
      month: String(month),
      view,
    })
    if (selectedDay) q.set('day', selectedDay)
    return `/api/calendar?${q.toString()}`
  }, [year, month, view, selectedDay])

  const { data, error } = useTelemetry<CalendarPayload>(path, 5000)
  const summary = data?.summary

  function shiftMonth(delta: number) {
    let m = month + delta
    let y = year
    if (m < 1) {
      m = 12
      y -= 1
    } else if (m > 12) {
      m = 1
      y += 1
    }
    setYear(y)
    setMonth(m)
    setSelectedDay(null)
  }

  function goToday() {
    const t = new Date()
    setYear(t.getFullYear())
    setMonth(t.getMonth() + 1)
    setSelectedDay(null)
    setView('month')
  }

  return (
    <div className="perf-layout">
      <section className="panel perf-calendar-panel">
        <div className="perf-calendar-head">
          <div>
            <p className="eyebrow">PERFORMANCE CALENDAR</p>
            <h2>{data?.month_label || 'Calendar'}</h2>
            {error ? <p className="negative" style={{ margin: '6px 0 0', fontSize: 11 }}>{error}</p> : null}
          </div>
          <div className="perf-controls">
            <button type="button" className="icon-button" onClick={() => (view === 'year' ? setYear(year - 1) : shiftMonth(-1))} aria-label="Previous">
              <ChevronLeft size={16} />
            </button>
            <strong className="mono">
              {view === 'year' ? `${year} (${money(summary?.net_pnl_usd)})` : data?.month_label || `${month}/${year}`}
            </strong>
            <button type="button" className="icon-button" onClick={() => (view === 'year' ? setYear(year + 1) : shiftMonth(1))} aria-label="Next">
              <ChevronRight size={16} />
            </button>
            <button type="button" className="button button-quiet" onClick={goToday}>
              Today
            </button>
          </div>
        </div>

        {view === 'month' && (
          <div className="perf-month-grid">
            <div className="perf-weekdays">
              {(data?.weekday_labels || ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']).map((d) => (
                <span key={d}>{d}</span>
              ))}
              <span>WEEKLY</span>
            </div>
            <div className="perf-weeks">
              {(data?.weeks || []).map((week, wi) => {
                const cells = (data?.days || []).slice(wi * 7, wi * 7 + 7)
                return (
                  <div className="perf-week-row" key={week.week_index}>
                    {cells.map((cell, ci) =>
                      !cell.in_month || !cell.date ? (
                        <div className="perf-cell empty" key={`e-${wi}-${ci}`} />
                      ) : (
                        <button
                          type="button"
                          key={cell.date}
                          className={`${cellClass(cell.pnl_usd || 0, cell.trades || 0)}${selectedDay === cell.date ? ' selected' : ''}`}
                          onClick={() => setSelectedDay(cell.date)}
                        >
                          <span className="perf-day-num">{cell.day}</span>
                          <span className="perf-day-pnl">{cell.trades ? money(cell.pnl_usd) : '—'}</span>
                          <span className="perf-day-meta">{cell.trades ? `${cell.trades}t` : ''}</span>
                        </button>
                      ),
                    )}
                    <div className={`perf-week-sum ${week.trades ? (week.pnl_usd >= 0 ? 'profit' : 'loss') : 'flat'}`}>
                      <b>{money(week.pnl_usd)}</b>
                      <small>{week.days_traded} days</small>
                    </div>
                  </div>
                )
              })}
            </div>
          </div>
        )}

        {view === 'week' && (
          <div className="perf-week-list">
            {(data?.weeks || []).map((week) => (
              <div className={`perf-week-card ${week.trades ? (week.pnl_usd >= 0 ? 'profit' : 'loss') : 'flat'}`} key={week.week_index}>
                <div>
                  <b>{week.label}</b>
                  <small className="muted">
                    {week.start} → {week.end}
                  </small>
                </div>
                <strong className="mono">{week.trades ? money(week.pnl_usd) : 'No trades'}</strong>
                <span className="muted">{week.trades} trades · {week.days_traded} days</span>
              </div>
            ))}
          </div>
        )}

        {view === 'year' && (
          <div className="perf-year-grid">
            {(data?.year_weeks || []).map((week) => (
              <div className={`perf-year-card ${week.empty ? 'flat' : week.pnl_usd >= 0 ? 'profit' : 'loss'}`} key={week.week_index}>
                <b>{week.label}</b>
                <small className="muted">
                  {week.start.slice(5)} – {week.end.slice(5)}
                </small>
                <strong className="mono">{week.empty ? 'No trades' : money(week.pnl_usd)}</strong>
              </div>
            ))}
          </div>
        )}

        <div className="perf-view-toggle">
          {(['month', 'week', 'year'] as const).map((v) => (
            <button key={v} type="button" className={view === v ? 'active' : ''} onClick={() => setView(v)}>
              {v === 'month' ? 'Daily View' : v === 'week' ? 'Weekly View' : 'Year View'}
            </button>
          ))}
        </div>

        {selectedDay && (
          <div className="perf-day-detail">
            <div className="section-title" style={{ marginBottom: 10 }}>
              <div className="section-heading">
                <span className="section-icon">
                  <CalendarDays size={15} />
                </span>
                <div>
                  <p className="eyebrow">SELECTED DAY (ICT)</p>
                  <h2>{selectedDay}</h2>
                </div>
              </div>
              <button type="button" className="button button-quiet" onClick={() => setSelectedDay(null)}>
                Clear
              </button>
            </div>
            {(data?.selected_day_trades || []).length === 0 ? (
              <p className="muted">No trades.</p>
            ) : (
              <div className="table-wrap">
                <table>
                  <thead>
                    <tr>
                      <th>TIME (ICT)</th>
                      <th>SYMBOL</th>
                      <th>P&L</th>
                      <th>R</th>
                      <th>OUTCOME</th>
                    </tr>
                  </thead>
                  <tbody>
                    {(data?.selected_day_trades || []).map((t, i) => (
                      <tr key={`${t.decision_id || i}`}>
                        <td className="muted mono">{formatIctTime(t.ts)}</td>
                        <td>
                          <b>{t.symbol}</b>
                        </td>
                        <td className={`mono ${t.pnl_usd >= 0 ? 'positive' : 'negative'}`}>{money(t.pnl_usd)}</td>
                        <td className="mono">{t.r_multiple.toFixed(2)}R</td>
                        <td className="muted">{t.outcome || '—'}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        )}
      </section>

      <aside className="perf-metrics">
        <div className="panel perf-metric">
          <span className="data-label">NET P&L</span>
          <strong className={`mono ${(summary?.net_pnl_usd || 0) >= 0 ? 'positive' : 'negative'}`}>{money(summary?.net_pnl_usd)}</strong>
        </div>
        <div className="panel perf-metric">
          <span className="data-label">WIN RATE</span>
          <strong className="mono">{summary?.trades ? `${((summary.win_rate || 0) * 100).toFixed(1)}%` : '—'}</strong>
          <small className="muted">
            {summary?.wins ?? 0}W / {summary?.losses ?? 0}L
          </small>
        </div>
        <div className="panel perf-metric">
          <span className="data-label">PROFIT FACTOR</span>
          <strong className="mono">{summary?.profit_factor == null ? '—' : summary.profit_factor.toFixed(2)}</strong>
        </div>
        <div className="panel perf-metric">
          <span className="data-label">EXPECTED VALUE</span>
          <strong className="mono">{money(summary?.expected_value_usd)}</strong>
        </div>
        <div className="panel perf-metric">
          <span className="data-label">AVG R</span>
          <strong className="mono">{(summary?.total_r ?? 0).toFixed(2)}R</strong>
          <small className="muted">{summary?.trades ?? 0} trades</small>
        </div>
        <div className="panel perf-metric">
          <span className="data-label">AVG WIN</span>
          <strong className="mono positive">{money(summary?.avg_win_usd)}</strong>
        </div>
        <div className="panel perf-metric">
          <span className="data-label">AVG LOSS</span>
          <strong className="mono negative">{money(summary?.avg_loss_usd)}</strong>
        </div>
      </aside>
    </div>
  )
}
