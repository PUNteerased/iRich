'use client'

import { useEffect, useMemo, useState } from 'react'
import {
  Activity,
  ArrowDownRight,
  ArrowUpRight,
  BarChart3,
  Bell,
  Bot,
  ChevronDown,
  Clock3,
  Cpu,
  Gauge,
  LayoutDashboard,
  ListFilter,
  Menu,
  MoreHorizontal,
  Radio,
  RefreshCw,
  Search,
  Settings2,
  ShieldCheck,
  Sparkles,
  Target,
  TerminalSquare,
  TrendingUp,
  TriangleAlert,
  WalletCards,
  X,
  Zap,
  CalendarDays,
} from 'lucide-react'
import { useLiveStream, useTelemetry } from '@/lib/useTelemetry'
import { formatIctDateTime, formatIctTime, nowIctLabel } from '@/lib/time'
import { AccountSwitcher } from '@/components/AccountSwitcher'
import { PerformanceCalendar } from '@/components/PerformanceCalendar'
import { HardwarePanel } from '@/components/HardwarePanel'

type PageKey =
  | 'overview'
  | 'analytics'
  | 'calendar'
  | 'trades'
  | 'execution'
  | 'risk'
  | 'models'
  | 'hardware'
  | 'system'
  | 'config'

const navItems: { key: PageKey; label: string; icon: typeof LayoutDashboard }[] = [
  { key: 'overview', label: 'Overview', icon: LayoutDashboard },
  { key: 'analytics', label: 'Analytics', icon: TrendingUp },
  { key: 'calendar', label: 'Performance calendar', icon: CalendarDays },
  { key: 'trades', label: 'Trade journal', icon: ListFilter },
  { key: 'execution', label: 'Execution stream', icon: Radio },
  { key: 'risk', label: 'Risk & breakers', icon: ShieldCheck },
  { key: 'models', label: 'Models & sentiment', icon: Sparkles },
  { key: 'hardware', label: 'Hardware', icon: Cpu },
  { key: 'system', label: 'System health', icon: Gauge },
  { key: 'config', label: 'System config', icon: Settings2 },
]

type DecisionRow = {
  ts?: string
  decision_id?: string
  symbol?: string
  code?: string
  detail?: string
  signal?: string
  side?: string
  prob?: number | string
  probability?: number | string
  model_prob?: number | string
}

type OverviewPayload = {
  ts?: string
  portfolio?: {
    available?: boolean
    balance?: number
    equity?: number
    free_margin?: number
    leverage?: number
    is_demo?: boolean
    server?: string
    login?: number
    currency?: string
    error?: string
    source?: string
    snapshot_age_sec?: number
  }
  position?: {
    ticket?: number
    symbol?: string
    side?: string
    entry?: number
    sl?: number
    tp?: number
    volume?: number
  } | null
  breaker?: Record<string, unknown>
  reject_tally?: { code: string; count: number; percent: number }[]
  decisions?: DecisionRow[]
  mm?: { mode?: string; option_a_lock?: boolean; lot?: number; max_risk_usd?: number }
  halt?: boolean
  active_symbols?: string[]
  data_source?: { portfolio?: string; snapshot_age_sec?: number }
}

function money(n: number | null | undefined, digits = 2): string {
  if (n == null || Number.isNaN(n)) return '—'
  return `$${n.toFixed(digits)}`
}

function probText(row: DecisionRow): string {
  const raw = row.prob ?? row.probability ?? row.model_prob
  if (raw == null) return '—'
  const n = typeof raw === 'number' ? raw : Number(raw)
  return Number.isFinite(n) ? n.toFixed(2) : String(raw)
}

function signalOf(row: DecisionRow): string {
  return String(row.signal || row.side || '—').toUpperCase()
}

function verdictOf(code?: string): 'ACCEPTED' | 'REJECTED' | 'OTHER' {
  if (!code) return 'OTHER'
  if (code.startsWith('REJECT')) return 'REJECTED'
  if (code === 'EXECUTED' || code === 'ACCEPTED' || code === 'OK') return 'ACCEPTED'
  return 'OTHER'
}

function StatusDot({ color = 'emerald' }: { color?: 'emerald' | 'amber' | 'rose' | 'indigo' }) {
  return <span className={`status-dot status-${color}`} aria-hidden="true" />
}

function Card({ children, className = '' }: { children: React.ReactNode; className?: string }) {
  return <section className={`panel ${className}`}>{children}</section>
}

function SectionTitle({
  icon: Icon,
  eyebrow,
  title,
  action,
}: {
  icon: typeof Activity
  eyebrow: string
  title: string
  action?: React.ReactNode
}) {
  return (
    <div className="section-title">
      <div className="section-heading">
        <span className="section-icon">
          <Icon size={15} />
        </span>
        <div>
          <p className="eyebrow">{eyebrow}</p>
          <h2>{title}</h2>
        </div>
      </div>
      {action}
    </div>
  )
}

function MetricCard({
  label,
  value,
  note,
  icon: Icon,
  accent = 'neutral',
  progress,
}: {
  label: string
  value: string
  note: string
  icon: typeof Activity
  accent?: string
  progress?: number
}) {
  return (
    <Card className="metric-card">
      <div className="metric-top">
        <span className="metric-label">{label}</span>
        <Icon size={16} className={`icon-${accent}`} />
      </div>
      <div className="metric-value">{value}</div>
      <div className="metric-note">{note}</div>
      {progress !== undefined && (
        <div className="progress-track">
          <div className={`progress-fill fill-${accent}`} style={{ width: `${Math.min(100, Math.max(0, progress))}%` }} />
        </div>
      )}
    </Card>
  )
}

function EmptyNote({ text }: { text: string }) {
  return <p className="muted" style={{ padding: '0.75rem 0' }}>{text}</p>
}

function DecisionTable({ rows, compact = false }: { rows: DecisionRow[]; compact?: boolean }) {
  const shown = compact ? rows.slice(-5).reverse() : [...rows].reverse()
  if (!shown.length) return <EmptyNote text="No decisions yet." />
  return (
    <div className="table-wrap">
      <table>
        <thead>
          <tr>
            <th>TIME (ICT)</th>
            <th>SYMBOL</th>
            <th>DECISION ID</th>
            <th>SIGNAL</th>
            <th>PROB.</th>
            <th>VERDICT</th>
            <th>DETAIL</th>
          </tr>
        </thead>
        <tbody>
          {shown.map((d) => {
            const verdict = verdictOf(d.code)
            const signal = signalOf(d)
            return (
              <tr key={d.decision_id || `${d.symbol}-${d.ts}`}>
                <td className="muted mono">{formatIctTime(d.ts)}</td>
                <td>
                  <b>{d.symbol}</b>
                </td>
                <td className="muted mono">{d.decision_id}</td>
                <td className={signal === 'BUY' ? 'positive' : signal === 'SELL' ? 'negative' : 'amber-text'}>{signal}</td>
                <td className="mono">{probText(d)}</td>
                <td>
                  <span className={`tag ${verdict === 'ACCEPTED' ? 'tag-green' : verdict === 'REJECTED' ? 'tag-red' : 'tag-gray'}`}>
                    {d.code || verdict}
                  </span>
                </td>
                <td className="muted">{d.detail || '—'}</td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}

function Overview({
  setPage,
  data,
  error,
  mode,
}: {
  setPage: (page: PageKey) => void
  data: OverviewPayload | null
  error: string | null
  mode: 'ws' | 'poll' | 'offline'
}) {
  const p = data?.portfolio
  const br = data?.breaker || {}
  const pos = data?.position
  const rejectRows = data?.reject_tally || []
  const consecutive = Number(br.consecutive_losses || 0)
  const maxConsec = 3

  return (
    <>
      <div className="page-heading">
        <div>
          <p className="eyebrow">OPERATIONS / LIVE</p>
          <h1>iRich monitor</h1>
          <p className="subheading">
            {error
              ? error
              : `${(data?.active_symbols || []).join(', ') || '—'}${
                  data?.portfolio?.login ? ` · ${data.portfolio.login}` : ''
                }`}
          </p>
        </div>
        <div className="heading-actions">
          <span className={`pill ${mode === 'offline' ? 'pill-amber' : 'pill-green'}`}>
            <StatusDot color={mode === 'offline' ? 'amber' : 'emerald'} />{' '}
            {mode === 'ws' ? 'LIVE 1Hz' : mode === 'poll' ? 'POLL 1s' : 'OFFLINE'}
          </span>
        </div>
      </div>
      <div className="metric-grid">
        <MetricCard
          label="Capital & equity"
          value={p?.available ? money(p.equity) : '—'}
          note={
            p?.available
              ? `Balance ${money(p.balance)} · ${p.is_demo ? 'DEMO' : 'REAL'} · ${p.server || ''}`
              : p?.error || 'MT5 portfolio unavailable'
          }
          icon={WalletCards}
          accent="emerald"
        />
        <MetricCard
          label="Circuit breakers"
          value={`${consecutive} / ${maxConsec}`}
          note={`Daily realized ${money(Number(br.daily_realized_usd || 0))} · monthly halt ${br.monthly_halted ? 'ON' : 'off'}`}
          icon={ShieldCheck}
          accent={consecutive > 0 ? 'amber' : 'emerald'}
          progress={(consecutive / maxConsec) * 100}
        />
        <MetricCard
          label="Free margin"
          value={p?.available ? money(p.free_margin) : '—'}
          note={p?.available ? `Leverage 1:${p.leverage || '?'} · Gate from broker` : 'Connect MT5 for live margin'}
          icon={BarChart3}
          accent="indigo"
        />
        <MetricCard
          label="Risk per trade"
          value={money(data?.mm?.max_risk_usd)}
          note={`${data?.mm?.mode || 'FIXED_TIER'} · ${data?.mm?.lot ?? 0.01} lot${data?.mm?.option_a_lock ? ' locked' : ''}`}
          icon={Target}
          accent="amber"
        />
      </div>

      <Card className="position-card">
        <div className="position-top">
          <SectionTitle
            icon={Zap}
            eyebrow="ACTIVE POSITION"
            title="1-position invariant"
            action={
              <span className={`pill ${pos ? 'pill-amber' : 'pill-green'}`}>
                <StatusDot color={pos ? 'amber' : 'emerald'} /> {pos ? 'OPEN' : 'FLAT'}
              </span>
            }
          />
        </div>
        {pos ? (
          <>
            <div className="position-grid">
              <div className="position-instrument">
                <span className="symbol-mark">{pos.symbol?.[0] || '?'}</span>
                <div>
                  <span className="data-label">SYMBOL & SIDE</span>
                  <strong>
                    {pos.side === 'BUY' ? <ArrowUpRight size={15} /> : <ArrowDownRight size={15} />} {pos.symbol}{' '}
                    <em>{pos.side}</em>
                  </strong>
                </div>
              </div>
              <div>
                <span className="data-label">TICKET</span>
                <strong className="mono">#{pos.ticket}</strong>
              </div>
              <div>
                <span className="data-label">ENTRY</span>
                <strong className="mono">{pos.entry}</strong>
              </div>
              <div>
                <span className="data-label">VOLUME</span>
                <strong className="mono">{pos.volume}</strong>
              </div>
              <div>
                <span className="data-label">SL / TP</span>
                <strong className="mono">
                  {pos.sl} / {pos.tp}
                </strong>
              </div>
            </div>
          </>
        ) : (
          <EmptyNote text="No open MT5 positions. Flat — waiting for next valid setup." />
        )}
      </Card>

      <div className="content-grid">
        <Card>
          <SectionTitle icon={ListFilter} eyebrow="TODAY'S FUNNEL" title="Reject reasons" />
          <div className="bars">
            {rejectRows.length === 0 && <EmptyNote text="No rejects tallied for today yet." />}
            {rejectRows.map((row) => (
              <div className="bar-row" key={row.code}>
                <div className="bar-label">
                  <span>{row.code}</span>
                  <b>
                    {row.count} <small>{row.percent}%</small>
                  </b>
                </div>
                <div className="bar-track">
                  <div style={{ width: `${row.percent}%` }} />
                </div>
              </div>
            ))}
          </div>
          <button className="text-button" onClick={() => setPage('execution')}>
            View all decisions <ArrowUpRight size={13} />
          </button>
        </Card>
        <Card className="radar-card">
          <SectionTitle
            icon={Sparkles}
            eyebrow="HALT / SAFETY"
            title="Ops flags"
            action={
              <span className="live-label">
                <StatusDot color={data?.halt ? 'rose' : 'emerald'} /> {data?.halt ? 'HALTED' : 'LIVE'}
              </span>
            }
          />
          <div className="sentiment">
            <div>
              <span className="data-label">KILL SWITCH FILE</span>
              <strong className={data?.halt ? 'negative' : 'positive'}>{data?.halt ? 'PRESENT' : 'NOT DETECTED'}</strong>
            </div>
            <div className="calendar-clear">
              <StatusDot /> Last overview sync <small>{formatIctDateTime(data?.ts)}</small>
            </div>
          </div>
        </Card>
      </div>

      <Card className="stream-card">
        <SectionTitle
          icon={TerminalSquare}
          eyebrow="LATEST SCANS"
          title="Live decision stream"
          action={
            <button className="button button-quiet" onClick={() => setPage('execution')}>
              Open audit log <ArrowUpRight size={14} />
            </button>
          }
        />
        <DecisionTable rows={data?.decisions || []} compact />
      </Card>
    </>
  )
}

function Execution() {
  const { data, refresh } = useTelemetry<{ items?: DecisionRow[]; count?: number; ts?: string }>('/api/decisions?limit=100')
  const { data: trades } = useTelemetry<{ items?: Record<string, unknown>[]; open_positions?: unknown[] }>('/api/trades?limit=50')
  const rows = data?.items || []
  const accepted = rows.filter((r) => verdictOf(r.code) === 'ACCEPTED').length
  const rejected = rows.filter((r) => verdictOf(r.code) === 'REJECTED').length

  return (
    <>
      <div className="page-heading">
        <div>
          <p className="eyebrow">AUDIT LOG / READ ONLY</p>
          <h1>Execution stream</h1>
          <p className="subheading">Decision stream</p>
        </div>
        <div className="heading-actions">
          <button className="button button-primary" onClick={() => void refresh()}>
            <RefreshCw size={14} /> Live polling
          </button>
        </div>
      </div>
      <div className="metric-grid three">
        <MetricCard label="Rows loaded" value={String(rows.length)} note={`Last sync ${formatIctTime(data?.ts)}`} icon={Activity} accent="indigo" />
        <MetricCard label="Accepted (window)" value={String(accepted)} note="EXECUTED / ACCEPTED in tail" icon={Zap} accent="emerald" />
        <MetricCard label="Rejected (window)" value={String(rejected)} note="Filtered safely by gates" icon={TriangleAlert} accent="rose" />
      </div>
      <Card className="stream-card">
        <SectionTitle
          icon={TerminalSquare}
          eyebrow="EVENTS / 3 SEC POLLING"
          title="Decision ledger"
          action={
            <span className="live-label">
              <StatusDot /> CONNECTED
            </span>
          }
        />
        <DecisionTable rows={rows} />
      </Card>
      <Card className="stream-card">
        <SectionTitle icon={TrendingUp} eyebrow="TRADES" title="Recent trades" />
        {(trades?.items || []).length === 0 ? (
          <EmptyNote text="No trades.jsonl rows yet." />
        ) : (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>TIME (ICT)</th>
                  <th>SYMBOL</th>
                  <th>STATUS</th>
                  <th>DETAIL</th>
                </tr>
              </thead>
              <tbody>
                {[...(trades?.items || [])].reverse().map((t, i) => (
                  <tr key={String(t.decision_id || i)}>
                    <td className="muted mono">{formatIctTime(String(t.ts || ''))}</td>
                    <td>
                      <b>{String(t.symbol || '')}</b>
                    </td>
                    <td>
                      <span className="tag tag-gray">{String(t.status || '')}</span>
                    </td>
                    <td className="muted mono">{JSON.stringify(t).slice(0, 120)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>
    </>
  )
}

function Risk() {
  const { data } = useTelemetry<{
    breaker?: Record<string, unknown>
    limits?: Record<string, number | boolean>
    halt?: boolean
  }>('/api/risk')
  const br = data?.breaker || {}
  const lim = data?.limits || {}
  const consec = Number(br.consecutive_losses || 0)
  const maxConsec = Number(lim.max_consecutive_losses || 3)
  const daily = Number(br.daily_realized_usd || 0)

  return (
    <>
      <div className="page-heading">
        <div>
          <p className="eyebrow">SAFETY / GUARDRAILS</p>
          <h1>Risk & breakers</h1>
          <p className="subheading">Breakers & limits</p>
        </div>
        <span className={`pill ${data?.halt ? 'pill-amber' : 'pill-green'}`}>
          <StatusDot color={data?.halt ? 'amber' : 'emerald'} /> {data?.halt ? 'HALT FILE ON' : 'GUARDS LOADED'}
        </span>
      </div>
      <div className="risk-grid">
        <MetricCard label="Daily realized" value={money(daily)} note="From breaker state" icon={ShieldCheck} accent="emerald" progress={0} />
        <MetricCard
          label="Monthly drawdown halt"
          value={br.monthly_halted ? 'LOCKED' : 'OPEN'}
          note={`Cap ${lim.monthly_max_dd_pct ?? 10}% · start equity ${br.month_start_equity ?? '—'}`}
          icon={BarChart3}
          accent={br.monthly_halted ? 'rose' : 'emerald'}
        />
        <MetricCard
          label="Loss streak"
          value={String(consec)}
          note={`of ${maxConsec} consecutive losses`}
          icon={TriangleAlert}
          accent="amber"
          progress={(consec / Math.max(1, maxConsec)) * 100}
        />
      </div>
      <Card className="breaker-panel">
        <SectionTitle icon={ShieldCheck} eyebrow="CIRCUIT BREAKER STATE" title="Protection matrix" />
        <div className="breaker-list">
          {[
            ['Daily loss gate', `daily_halted=${Boolean(br.daily_halted)}`, br.daily_halted ? 'LOCKED' : 'PASSED', br.daily_halted ? 'rose' : 'emerald'],
            ['Monthly drawdown gate', `monthly_halted=${Boolean(br.monthly_halted)}`, br.monthly_halted ? 'LOCKED' : 'PASSED', br.monthly_halted ? 'rose' : 'emerald'],
            ['Consecutive loss gate', `${consec} / ${maxConsec}`, br.consecutive_halted ? 'LOCKED' : 'PASSED', br.consecutive_halted ? 'rose' : 'emerald'],
            ['One-position invariant', `max_open=${lim.max_total_open ?? 1}`, 'LOCKED', 'amber'],
            ['Lot / option_a_lock', `${lim.lot_volume} lot · risk ${lim.max_risk_usd}`, lim.option_a_lock ? 'LOCKED' : 'TIERED', 'amber'],
            ['HALT file', data?.halt ? 'Detected' : 'Not detected', data?.halt ? 'ON' : 'OFF', data?.halt ? 'rose' : 'gray'],
          ].map(([name, desc, state, color]) => (
            <div className="breaker-row" key={String(name)}>
              <span className={`breaker-icon br-${color}`}>
                <ShieldCheck size={16} />
              </span>
              <span>
                <b>{name}</b>
                <small>{desc}</small>
              </span>
              <strong className={`text-${color}`}>{state}</strong>
              <ChevronDown size={15} className="muted" />
            </div>
          ))}
        </div>
      </Card>
    </>
  )
}

function Models() {
  const { data } = useTelemetry<{
    items?: {
      symbol: string
      exists: boolean
      model_mtime?: string
      model_sha256?: string
      last_code?: string
      last_detail?: string
      last_ts?: string
      last_prob?: number | string
      last_signal?: string
    }[]
    min_probability?: number
    ts?: string
  }>('/api/models')

  return (
    <>
      <div className="page-heading">
        <div>
          <p className="eyebrow">INTELLIGENCE / XGBOOST</p>
          <h1>Models & last signals</h1>
          <p className="subheading">Model status</p>
        </div>
        <button className="button button-quiet">
          <Clock3 size={14} /> {formatIctDateTime(data?.ts)}
        </button>
      </div>
      <div className="model-grid">
        {(data?.items || []).map((m) => {
          const verdict = verdictOf(m.last_code)
          const width = `${Math.round(Math.min(100, Math.max(0, Number(m.last_prob) || 0) * 100))}%`
          return (
            <Card className="model-card" key={m.symbol}>
              <div className="model-top">
                <span className="coin coin-eur">{m.symbol.slice(0, 2)}</span>
                <span>
                  <b>{m.symbol}</b>
                  <small>{m.exists ? `sha ${m.model_sha256 || '—'}` : 'missing pkl'}</small>
                </span>
                <MoreHorizontal size={16} className="muted" />
              </div>
              <div className="model-confidence">
                <strong>{m.last_prob != null ? Number(m.last_prob).toFixed?.(2) ?? m.last_prob : '—'}</strong>
                <span>last prob</span>
              </div>
              <div className="bar-track">
                <div className={`fill-${verdict === 'ACCEPTED' ? 'emerald' : verdict === 'REJECTED' ? 'rose' : 'indigo'}`} style={{ width }} />
              </div>
              <div className="model-bottom">
                <span className={`tag ${verdict === 'ACCEPTED' ? 'tag-green' : verdict === 'REJECTED' ? 'tag-red' : 'tag-gray'}`}>
                  {m.last_code || 'IDLE'}
                </span>
                <b className="muted">{formatIctTime(m.last_ts)}</b>
              </div>
            </Card>
          )
        })}
      </div>
      <Card className="sentiment-panel">
        <SectionTitle icon={Sparkles} eyebrow="THRESHOLD" title="Sniper gate" />
        <div className="sentiment-big">
          <div>
            <span className="data-label">MIN PROBABILITY</span>
            <strong>{data?.min_probability ?? 0.75}</strong>
            <small>from config.yaml</small>
          </div>
        </div>
      </Card>
    </>
  )
}

function System({
  data,
  error,
  mode,
}: {
  data: {
    ok?: boolean
    halt?: boolean
    decisions_age_sec?: number | null
    trades_age_sec?: number | null
    decisions_exists?: boolean
    trades_exists?: boolean
    ts?: string
    option_a_lock?: boolean
  } | null
  error: string | null
  mode: 'ws' | 'poll' | 'offline'
}) {
  return (
    <>
      <div className="page-heading">
        <div>
          <p className="eyebrow">INFRASTRUCTURE / TELEMETRY</p>
          <h1>System health</h1>
          <p className="subheading">Bridge status</p>
        </div>
        <span className={`pill ${error ? 'pill-amber' : 'pill-green'}`}>
          <StatusDot color={error ? 'amber' : 'emerald'} /> {error ? 'API OFFLINE' : 'SYSTEM ONLINE'}
        </span>
      </div>
      <div className="system-grid">
        <Card>
          <SectionTitle icon={Bot} eyebrow="LOCAL BRIDGE" title="Connection pipeline" />
          <div className="pipeline">
            {[
              ['iRich trading engine', 'python src/main.py + MT5', error ? 'UNKNOWN' : 'EXPECTED'],
              ['Telemetry bridge', 'FastAPI · port 8000', error ? 'DOWN' : 'ONLINE'],
              [
                'Dashboard client',
                mode === 'ws' ? 'WebSocket live · 1 Hz' : mode === 'poll' ? 'HTTP poll · 1s' : 'Disconnected',
                error ? 'RETRYING' : mode === 'ws' ? 'LIVE' : 'CONNECTED',
              ],
              ['Display timezone', 'Asia/Bangkok (ICT)', 'LOCKED'],
            ].map(([name, desc, status], i) => (
              <div className="pipeline-step" key={String(name)}>
                <span className="step-number">0{i + 1}</span>
                <span>
                  <b>{name}</b>
                  <small>{desc}</small>
                </span>
                <strong className="text-emerald">{status}</strong>
                {i < 3 && <span className="pipeline-line" />}
              </div>
            ))}
          </div>
        </Card>
        <Card>
          <SectionTitle icon={Activity} eyebrow="RUNTIME" title="Telemetry snapshot" />
          <div className="runtime-list">
            <div>
              <span>Last heartbeat</span>
              <b className="mono">{formatIctDateTime(data?.ts)}</b>
            </div>
            <div>
              <span>decisions.jsonl age</span>
              <b className="mono">{data?.decisions_age_sec != null ? `${Math.round(data.decisions_age_sec)}s` : '—'}</b>
            </div>
            <div>
              <span>trades.jsonl</span>
              <b className="mono">{data?.trades_exists ? 'present' : 'missing'}</b>
            </div>
            <div>
              <span>HALT flag</span>
              <b className={data?.halt ? 'negative mono' : 'positive mono'}>{data?.halt ? 'DETECTED' : 'NOT DETECTED'}</b>
            </div>
            <div>
              <span>option_a_lock</span>
              <b className="mono">{String(Boolean(data?.option_a_lock))}</b>
            </div>
          </div>
        </Card>
      </div>
      <Card className="notice-card">
        <StatusDot color={error ? 'rose' : 'emerald'} />
        <div>
          <b>{error ? 'Bridge offline' : 'Bridge online'}</b>
        </div>
      </Card>
    </>
  )
}

function CalendarPage() {
  return (
    <>
      <div className="page-heading">
        <div>
          <p className="eyebrow">PERFORMANCE / ICT</p>
          <h1>Performance calendar</h1>
          <p className="subheading">P&amp;L by day / week</p>
        </div>
      </div>
      <PerformanceCalendar />
    </>
  )
}

function Analytics() {
  const { data } = useTelemetry<{
    total_trades?: number
    wins?: number
    losses?: number
    total_pnl_usd?: number
    total_r?: number
    items?: Record<string, unknown>[]
  }>('/api/analytics')

  return (
    <>
      <div className="page-heading">
        <div>
          <p className="eyebrow">PERFORMANCE / JOURNAL</p>
          <h1>Analytics</h1>
          <p className="subheading">Closed trades</p>
        </div>
      </div>
      <div className="metric-grid three">
        <MetricCard
          label="Total closed trades"
          value={String(data?.total_trades ?? 0)}
          note={`${data?.wins ?? 0} wins · ${data?.losses ?? 0} losses`}
          icon={Activity}
          accent="indigo"
        />
        <MetricCard label="Total R" value={`${(data?.total_r ?? 0) >= 0 ? '+' : ''}${(data?.total_r ?? 0).toFixed(2)}R`} note={`PnL ${money(data?.total_pnl_usd)}`} icon={TrendingUp} accent="emerald" />
        <MetricCard label="Sample size" value={String((data?.items || []).length)} note="Recent closed rows shown" icon={Target} accent="amber" />
      </div>
      <Card className="stream-card">
        <SectionTitle icon={TerminalSquare} eyebrow="CLOSED TRADES" title="Journal sample" />
        {(data?.items || []).length === 0 ? (
          <EmptyNote text="No closed trades recorded yet." />
        ) : (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>TIME (ICT)</th>
                  <th>SYMBOL</th>
                  <th>STATUS</th>
                  <th>P&L</th>
                  <th>R</th>
                </tr>
              </thead>
              <tbody>
                {[...(data?.items || [])].reverse().map((t, i) => (
                  <tr key={String(t.decision_id || i)}>
                    <td className="muted mono">{formatIctTime(String(t.ts || ''))}</td>
                    <td>
                      <b>{String(t.symbol || '')}</b>
                    </td>
                    <td>{String(t.status || '')}</td>
                    <td className="mono">{t.pnl_usd != null ? money(Number(t.pnl_usd)) : '—'}</td>
                    <td className="mono">{t.r_multiple != null ? String(t.r_multiple) : '—'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>
    </>
  )
}

function Trades() {
  const { data } = useTelemetry<{
    items?: Record<string, unknown>[]
    open_positions?: { symbol?: string; side?: string; ticket?: number; entry?: number }[]
  }>('/api/trades?limit=100')

  return (
    <>
      <div className="page-heading">
        <div>
          <p className="eyebrow">JOURNAL</p>
          <h1>Trade journal</h1>
          <p className="subheading">Open & closed trades</p>
        </div>
      </div>
      <Card className="stream-card">
        <SectionTitle icon={Zap} eyebrow="OPEN" title="Live positions" action={<span className="muted">{(data?.open_positions || []).length} open</span>} />
        {(data?.open_positions || []).length === 0 ? (
          <EmptyNote text="Flat — no open positions." />
        ) : (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>TICKET</th>
                  <th>SYMBOL</th>
                  <th>SIDE</th>
                  <th>ENTRY</th>
                </tr>
              </thead>
              <tbody>
                {(data?.open_positions || []).map((p) => (
                  <tr key={p.ticket}>
                    <td className="mono">#{p.ticket}</td>
                    <td>
                      <b>{p.symbol}</b>
                    </td>
                    <td className={p.side === 'BUY' ? 'positive' : 'negative'}>{p.side}</td>
                    <td className="mono">{p.entry}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>
      <Card className="stream-card">
        <SectionTitle icon={TerminalSquare} eyebrow="JOURNAL" title="Closed trades" action={<span className="muted">{(data?.items || []).length}</span>} />
        {(data?.items || []).length === 0 ? (
          <EmptyNote text="No trade journal rows yet." />
        ) : (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>TIME (ICT)</th>
                  <th>SYMBOL</th>
                  <th>STATUS</th>
                  <th>DECISION ID</th>
                </tr>
              </thead>
              <tbody>
                {[...(data?.items || [])].reverse().map((t, i) => (
                  <tr key={String(t.decision_id || i)}>
                    <td className="muted mono">{formatIctTime(String(t.ts || ''))}</td>
                    <td>
                      <b>{String(t.symbol || '')}</b>
                    </td>
                    <td>
                      <span className="tag tag-gray">{String(t.status || '')}</span>
                    </td>
                    <td className="muted mono">{String(t.decision_id || '')}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>
    </>
  )
}

function Config() {
  const { data } = useTelemetry<{
    params?: Record<string, string | number | boolean>
    spread_caps?: Record<string, { max_points?: number | null; avg_points?: number | null; configured?: unknown }>
    calibration_updated_at?: string
    calibration_method?: string
    symbols?: { all?: string[]; weekday?: string[]; weekend?: string[] }
  }>('/api/config')

  const params = Object.entries(data?.params || {})

  return (
    <>
      <div className="page-heading">
        <div>
          <p className="eyebrow">CONFIG.YAML / ACTIVE PROFILE</p>
          <h1>System config</h1>
          <p className="subheading">Active parameters</p>
        </div>
        <span className="pill pill-green">
          <StatusDot /> CONFIG SYNCED
        </span>
      </div>
      <div className="config-grid">
        <Card>
          <SectionTitle icon={Settings2} eyebrow="LIVE PARAMETERS" title="Active configuration" />
          <div className="config-list">
            {params.length === 0 && <EmptyNote text="No data." />}
            {params.map(([name, value]) => (
              <div key={name}>
                <span>{name}</span>
                <b className="mono">{String(value)}</b>
              </div>
            ))}
          </div>
        </Card>
        <Card>
          <SectionTitle icon={BarChart3} eyebrow="SPREAD CALIBRATION" title="Broker caps" />
          <div className="spread-status">
            {Object.entries(data?.spread_caps || {}).map(([symbol, cap]) => (
              <div key={symbol}>
                <span className="data-label">{symbol} MAX POINTS</span>
                <strong>{cap.max_points ?? '—'}</strong>
                <span className="tag tag-gray">{String(cap.configured ?? '')}</span>
              </div>
            ))}
            <div>
              <span className="data-label">CALIBRATION</span>
              <strong className="mono">{data?.calibration_method || '—'}</strong>
              <span className="tag tag-gray">{formatIctDateTime(data?.calibration_updated_at)}</span>
            </div>
          </div>
        </Card>
      </div>
      <Card className="model-health">
        <SectionTitle icon={Sparkles} eyebrow="SYMBOL SETS" title="Weekday / weekend" />
        <div className="health-grid">
          <div>
            <span className="data-label">ALL</span>
            <b className="mono">{(data?.symbols?.all || []).join(', ') || '—'}</b>
          </div>
          <div>
            <span className="data-label">WEEKDAY</span>
            <b className="mono">{(data?.symbols?.weekday || []).join(', ') || '—'}</b>
          </div>
          <div>
            <span className="data-label">WEEKEND</span>
            <b className="mono">{(data?.symbols?.weekend || []).join(', ') || '—'}</b>
          </div>
        </div>
      </Card>
    </>
  )
}

export default function Page() {
  const [page, setPage] = useState<PageKey>('overview')
  const [mobileNav, setMobileNav] = useState(false)
  // Empty until mount — avoids SSR/client clock mismatch (hydration).
  const [clock, setClock] = useState('--:--:-- ICT')
  const live = useLiveStream(1000)
  const health = live.health
  const current = useMemo(() => navItems.find((item) => item.key === page) ?? navItems[0], [page])

  useEffect(() => {
    setClock(nowIctLabel())
    const id = window.setInterval(() => setClock(nowIctLabel()), 1000)
    return () => window.clearInterval(id)
  }, [])
  const content =
    page === 'overview' ? (
      <Overview
        setPage={setPage}
        data={(live.overview as OverviewPayload | null) ?? null}
        error={live.error}
        mode={live.mode}
      />
    ) : page === 'analytics' ? (
      <Analytics />
    ) : page === 'calendar' ? (
      <CalendarPage />
    ) : page === 'trades' ? (
      <Trades />
    ) : page === 'execution' ? (
      <Execution />
    ) : page === 'risk' ? (
      <Risk />
    ) : page === 'models' ? (
      <Models />
    ) : page === 'hardware' ? (
      <HardwarePanel data={live.hardware} mode={live.mode} error={live.error} />
    ) : page === 'system' ? (
      <System data={health} error={live.error} mode={live.mode} />
    ) : (
      <Config />
    )

  return (
    <div className="app-shell">
      <aside className={`sidebar ${mobileNav ? 'sidebar-open' : ''}`}>
        <div className="brand">
          <div className="brand-mark">
            <Activity size={18} />
          </div>
          <div>
            <strong>iRich</strong>
            <span>quant monitor</span>
          </div>
          <button className="mobile-close" onClick={() => setMobileNav(false)} aria-label="Close navigation">
            <X size={17} />
          </button>
        </div>
        <AccountSwitcher />
        <nav className="nav">
          <span className="nav-label">MONITORING</span>
          {navItems.map((item) => (
            <button
              key={item.key}
              className={`nav-item ${page === item.key ? 'active' : ''}`}
              onClick={() => {
                setPage(item.key)
                setMobileNav(false)
              }}
            >
              <item.icon size={16} />
              <span>{item.label}</span>
            </button>
          ))}
        </nav>
        <div className="sidebar-bottom">
          <button className="nav-item" onClick={() => setPage('config')}>
            <Settings2 size={16} />
            <span>Settings</span>
          </button>
          <div className="engine-status">
            <StatusDot color={health?.ok ? 'emerald' : 'rose'} />
            <span>
              <b>{health?.ok ? 'Bridge online' : 'Bridge offline'}</b>
              <small>
                {live.mode === 'ws' ? 'WS live' : live.mode === 'poll' ? 'Poll 1s' : health?.halt ? 'HALT' : 'Offline'}
              </small>
            </span>
            <MoreHorizontal size={15} className="muted" />
          </div>
        </div>
      </aside>
      <main className="main">
        <header className="topbar">
          <button className="mobile-menu" onClick={() => setMobileNav(true)} aria-label="Open navigation">
            <Menu size={19} />
          </button>
          <div className="crumb">
            <span>iRich OS</span>
            <span>/</span>
            <b>{current.label}</b>
          </div>
          <div className="topbar-actions">
            <div className="server-time">
              <span>
                <StatusDot color={live.mode === 'offline' ? 'rose' : 'emerald'} />{' '}
                {live.mode === 'ws' ? 'LIVE WS' : live.mode === 'poll' ? 'LIVE POLL' : 'OFFLINE'} · ICT
              </span>
              <b>{clock}</b>
            </div>
            <button className="icon-button" aria-label="Search">
              <Search size={17} />
            </button>
            <button className="icon-button notification" aria-label="Notifications">
              <Bell size={17} />
              <i />
            </button>
            <div className="user-avatar">IR</div>
          </div>
        </header>
        <div className="page-content">
          {content}
          <footer>
            <span>iRich OS · Final v1.5</span>
            <span>ICT · Asia/Bangkok</span>
            <span>© 2026 iRich</span>
          </footer>
        </div>
      </main>
    </div>
  )
}
