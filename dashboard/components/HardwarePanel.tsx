'use client'

import { Activity, Cpu, HardDrive, Monitor, Radio, Thermometer, Zap } from 'lucide-react'
import type { THardware } from '@/lib/useTelemetry'
import { formatIctDateTime } from '@/lib/time'

function Meter({ pct, tone = 'indigo' }: { pct: number; tone?: 'indigo' | 'green' | 'amber' | 'rose' }) {
  const v = Math.max(0, Math.min(100, pct || 0))
  return (
    <div className="hw-meter">
      <div className={`hw-meter-fill tone-${tone}`} style={{ width: `${v}%` }} />
    </div>
  )
}

function fmtUptime(sec?: number) {
  if (sec == null || !Number.isFinite(sec)) return '—'
  const d = Math.floor(sec / 86400)
  const h = Math.floor((sec % 86400) / 3600)
  const m = Math.floor((sec % 3600) / 60)
  if (d > 0) return `${d}d ${h}h ${m}m`
  if (h > 0) return `${h}h ${m}m`
  return `${m}m`
}

function fmtBytes(n?: number) {
  if (n == null) return '—'
  if (n < 1024) return `${n} B`
  if (n < 1024 ** 2) return `${(n / 1024).toFixed(1)} KB`
  if (n < 1024 ** 3) return `${(n / 1024 ** 2).toFixed(1)} MB`
  return `${(n / 1024 ** 3).toFixed(2)} GB`
}

function toneFor(pct: number): 'green' | 'amber' | 'rose' | 'indigo' {
  if (pct >= 90) return 'rose'
  if (pct >= 70) return 'amber'
  if (pct >= 40) return 'indigo'
  return 'green'
}

export function HardwarePanel({
  data,
  mode,
  error,
}: {
  data: THardware | null
  mode: 'ws' | 'poll' | 'offline'
  error: string | null
}) {
  const online = Boolean(data?.available) && !error
  const cpu = data?.cpu
  const mem = data?.memory
  const gpus = data?.gpu || []

  return (
    <>
      <div className="page-heading">
        <div>
          <p className="eyebrow">INFRASTRUCTURE / HOST</p>
          <h1>Hardware</h1>
          <p className="subheading">
            {data?.hostname || 'Host'} · live computer telemetry
          </p>
        </div>
        <span className={`pill ${online ? 'pill-green' : 'pill-amber'}`}>
          <span className={`status-dot ${online ? 'status-emerald' : 'status-amber'}`} />
          {mode === 'ws' ? 'LIVE WS' : mode === 'poll' ? 'LIVE POLL' : 'OFFLINE'}
        </span>
      </div>

      {!online && (
        <div className="panel notice-card" style={{ marginBottom: 12 }}>
          <span className="status-dot status-rose" />
          <div>
            <b>{error || data?.error || 'Hardware offline'}</b>
            <small style={{ display: 'block', color: 'var(--muted)', marginTop: 4 }}>
              {data?.hint || 'Ensure telemetry_api is running and psutil is installed.'}
            </small>
          </div>
        </div>
      )}

      <div className="metric-grid" style={{ marginBottom: 12 }}>
        <div className="panel metric-card">
          <span className="data-label">CPU LOAD</span>
          <strong className="mono">{(cpu?.pct ?? 0).toFixed(1)}%</strong>
          <Meter pct={cpu?.pct ?? 0} tone={toneFor(cpu?.pct ?? 0)} />
          <small className="muted">{cpu?.name || '—'}</small>
        </div>
        <div className="panel metric-card">
          <span className="data-label">MEMORY</span>
          <strong className="mono">
            {(mem?.used_gb ?? 0).toFixed(1)} / {(mem?.total_gb ?? 0).toFixed(1)} GB
          </strong>
          <Meter pct={mem?.pct ?? 0} tone={toneFor(mem?.pct ?? 0)} />
          <small className="muted">{(mem?.pct ?? 0).toFixed(1)}% allocated</small>
        </div>
        <div className="panel metric-card">
          <span className="data-label">GPU</span>
          <strong className="mono">
            {gpus[0] ? `${gpus[0].util_pct?.toFixed(0)}%` : 'N/A'}
          </strong>
          <Meter pct={gpus[0]?.util_pct ?? 0} tone={toneFor(gpus[0]?.util_pct ?? 0)} />
          <small className="muted">{gpus[0]?.name || 'No NVIDIA / nvidia-smi'}</small>
        </div>
        <div className="panel metric-card">
          <span className="data-label">VRAM</span>
          <strong className="mono">
            {gpus[0]
              ? `${Math.round(gpus[0].vram_used_mb || 0)} / ${Math.round(gpus[0].vram_total_mb || 0)} MB`
              : '—'}
          </strong>
          <Meter
            pct={
              gpus[0]?.vram_total_mb
                ? (100 * (gpus[0].vram_used_mb || 0)) / gpus[0].vram_total_mb
                : 0
            }
            tone={toneFor(
              gpus[0]?.vram_total_mb
                ? (100 * (gpus[0].vram_used_mb || 0)) / gpus[0].vram_total_mb
                : 0,
            )}
          />
          <small className="muted">
            {gpus[0]?.temp_c != null ? `${gpus[0].temp_c}°C` : 'temp n/a'}
            {gpus[0]?.power_w != null ? ` · ${gpus[0].power_w.toFixed(0)} W` : ''}
          </small>
        </div>
      </div>

      <div className="content-grid" style={{ marginBottom: 12 }}>
        <div className="panel">
          <div className="section-title">
            <div className="section-heading">
              <span className="section-icon">
                <Cpu size={15} />
              </span>
              <div>
                <p className="eyebrow">CPU CORES</p>
                <h2>Per-core load</h2>
              </div>
            </div>
            <span className="live-label">
              <span className="status-dot status-emerald" /> {mode === 'ws' ? 'WS 1Hz' : 'POLL'}
            </span>
          </div>
          <div className="hw-cores">
            {(cpu?.per_cpu || []).map((p, i) => (
              <div key={i} className="hw-core">
                <span>C{i}</span>
                <Meter pct={p} tone={toneFor(p)} />
                <b className="mono">{p.toFixed(0)}%</b>
              </div>
            ))}
            {!cpu?.per_cpu?.length && <p className="muted">No core samples yet.</p>}
          </div>
          <div className="runtime-list" style={{ marginTop: 16 }}>
            <div>
              <span>Physical / logical</span>
              <b className="mono">
                {cpu?.physical_cores ?? '—'} / {cpu?.logical_cores ?? '—'}
              </b>
            </div>
            <div>
              <span>Frequency</span>
              <b className="mono">{cpu?.freq_mhz != null ? `${Math.round(cpu.freq_mhz)} MHz` : '—'}</b>
            </div>
          </div>
        </div>

        <div className="panel">
          <div className="section-title">
            <div className="section-heading">
              <span className="section-icon">
                <HardDrive size={15} />
              </span>
              <div>
                <p className="eyebrow">STORAGE</p>
                <h2>Disk volumes</h2>
              </div>
            </div>
          </div>
          <div className="hw-disks">
            {(data?.disks || []).map((d) => (
              <div key={`${d.device}-${d.mount}`} className="hw-disk">
                <div className="bar-label">
                  <b>{d.mount || d.device}</b>
                  <small>
                    {d.used_gb}/{d.total_gb} GB · {d.fstype || ''}
                  </small>
                </div>
                <Meter pct={d.pct ?? 0} tone={toneFor(d.pct ?? 0)} />
              </div>
            ))}
            {!data?.disks?.length && <p className="muted">No disks reported.</p>}
          </div>
        </div>
      </div>

      <div className="content-grid" style={{ marginBottom: 12 }}>
        <div className="panel">
          <div className="section-title">
            <div className="section-heading">
              <span className="section-icon">
                <Monitor size={15} />
              </span>
              <div>
                <p className="eyebrow">HOST</p>
                <h2>Core details</h2>
              </div>
            </div>
          </div>
          <div className="runtime-list">
            <div>
              <span>OS</span>
              <b className="mono">
                {data?.os?.system} {data?.os?.release}
              </b>
            </div>
            <div>
              <span>Machine</span>
              <b className="mono">{data?.os?.machine || '—'}</b>
            </div>
            <div>
              <span>Python</span>
              <b className="mono">{data?.os?.python || '—'}</b>
            </div>
            <div>
              <span>Boot</span>
              <b className="mono">{formatIctDateTime(data?.boot_time)}</b>
            </div>
            <div>
              <span>Uptime</span>
              <b className="mono">{fmtUptime(data?.uptime_sec)}</b>
            </div>
            <div>
              <span>API uptime</span>
              <b className="mono">{fmtUptime(data?.api_uptime_sec)}</b>
            </div>
            <div>
              <span>Net recv / sent</span>
              <b className="mono">
                {fmtBytes(data?.network?.bytes_recv)} / {fmtBytes(data?.network?.bytes_sent)}
              </b>
            </div>
            {data?.battery && (
              <div>
                <span>Battery</span>
                <b className="mono">
                  {data.battery.pct?.toFixed(0)}% {data.battery.plugged ? 'AC' : 'BAT'}
                </b>
              </div>
            )}
          </div>
        </div>

        <div className="panel">
          <div className="section-title">
            <div className="section-heading">
              <span className="section-icon">
                <Thermometer size={15} />
              </span>
              <div>
                <p className="eyebrow">THERMALS</p>
                <h2>Sensors</h2>
              </div>
            </div>
          </div>
          <div className="runtime-list">
            {Object.keys(data?.temps_c || {}).length === 0 && (
              <p className="muted" style={{ padding: '0.5rem 0' }}>
                No temperature sensors exposed by OS (common on Windows without admin).
              </p>
            )}
            {Object.entries(data?.temps_c || {}).map(([k, v]) => (
              <div key={k}>
                <span>{k}</span>
                <b className="mono">{v.toFixed(1)} °C</b>
              </div>
            ))}
            <div>
              <span>Swap</span>
              <b className="mono">
                {(mem?.swap_used_gb ?? 0).toFixed(1)} / {(mem?.swap_total_gb ?? 0).toFixed(1)} GB (
                {(mem?.swap_pct ?? 0).toFixed(0)}%)
              </b>
            </div>
            <div>
              <span>Sample ts</span>
              <b className="mono">{formatIctDateTime(data?.ts)}</b>
            </div>
          </div>
        </div>
      </div>

      <div className="panel">
        <div className="section-title">
          <div className="section-heading">
            <span className="section-icon">
              <Activity size={15} />
            </span>
            <div>
              <p className="eyebrow">IRICH PROCESSES</p>
              <h2>Engine / bridge / MT5 / ngrok</h2>
            </div>
          </div>
          <span className="live-label">
            <Radio size={12} /> {mode === 'offline' ? 'CLOSED' : 'OPEN'}
          </span>
        </div>
        <div className="hw-proc-table">
          <div className="hw-proc-head">
            <span>PID</span>
            <span>Name</span>
            <span>CPU</span>
            <span>RSS</span>
            <span>Command</span>
          </div>
          {(data?.processes || []).map((p) => (
            <div className="hw-proc-row" key={p.pid}>
              <span className="mono">{p.pid}</span>
              <span>{p.name}</span>
              <span className="mono">{(p.cpu_pct ?? 0).toFixed(1)}%</span>
              <span className="mono">{(p.rss_mb ?? 0).toFixed(0)} MB</span>
              <span className="muted mono" title={p.cmdline}>
                {p.cmdline || '—'}
              </span>
            </div>
          ))}
          {!data?.processes?.length && (
            <p className="muted" style={{ padding: '0.75rem 0' }}>
              No matching iRich / MT5 / ngrok processes found.
            </p>
          )}
        </div>
      </div>
    </>
  )
}

export function HardwareLiveBadge({ mode }: { mode: 'ws' | 'poll' | 'offline' }) {
  return (
    <span className="live-label" style={{ gap: 6 }}>
      <Zap size={12} />
      {mode === 'ws' ? 'WebSocket live' : mode === 'poll' ? 'HTTP poll 1s' : 'Disconnected'}
    </span>
  )
}
