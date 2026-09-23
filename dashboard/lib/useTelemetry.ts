'use client'

import { useCallback, useEffect, useRef, useState } from 'react'
import { fetchTelemetry } from './api'
import { telemetryWsUrl } from './ws'

type LiveTick = {
  type?: string
  health?: THealth
  overview?: unknown
  hardware?: THardware
}

export type THealth = {
  ok?: boolean
  halt?: boolean
  ts?: string
  decisions_age_sec?: number | null
  trades_age_sec?: number | null
  decisions_exists?: boolean
  trades_exists?: boolean
  option_a_lock?: boolean
}

export type THardware = {
  available?: boolean
  error?: string
  hint?: string
  ts?: string
  hostname?: string
  os?: {
    system?: string
    release?: string
    version?: string
    machine?: string
    python?: string
  }
  cpu?: {
    name?: string
    physical_cores?: number | null
    logical_cores?: number | null
    freq_mhz?: number | null
    pct?: number
    per_cpu?: number[]
  }
  memory?: {
    total_gb?: number
    used_gb?: number
    available_gb?: number
    pct?: number
    swap_total_gb?: number
    swap_used_gb?: number
    swap_pct?: number
  }
  disks?: {
    device?: string
    mount?: string
    fstype?: string
    total_gb?: number
    used_gb?: number
    free_gb?: number
    pct?: number
  }[]
  network?: {
    bytes_sent?: number
    bytes_recv?: number
    packets_sent?: number
    packets_recv?: number
  }
  gpu?: {
    name?: string
    util_pct?: number
    mem_util_pct?: number
    vram_total_mb?: number
    vram_used_mb?: number
    temp_c?: number
    power_w?: number | null
  }[]
  temps_c?: Record<string, number>
  battery?: { pct?: number; plugged?: boolean; secs_left?: number | null } | null
  boot_time?: string
  uptime_sec?: number
  api_uptime_sec?: number
  processes?: {
    pid?: number
    name?: string
    cmdline?: string
    cpu_pct?: number
    rss_mb?: number
  }[]
}

/**
 * Real-time bridge: prefer WebSocket /ws/live, fall back to HTTP poll.
 */
export function useLiveStream(pollMs = 1000) {
  const [health, setHealth] = useState<THealth | null>(null)
  const [overview, setOverview] = useState<unknown>(null)
  const [hardware, setHardware] = useState<THardware | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [mode, setMode] = useState<'ws' | 'poll' | 'offline'>('offline')
  const [updatedAt, setUpdatedAt] = useState<string | null>(null)
  const wsRef = useRef<WebSocket | null>(null)
  const pollRef = useRef<number | null>(null)

  const applyTick = useCallback((tick: LiveTick) => {
    if (tick.health) setHealth(tick.health)
    if (tick.overview) setOverview(tick.overview)
    if (tick.hardware) setHardware(tick.hardware)
    setError(null)
    setUpdatedAt(new Date().toISOString())
  }, [])

  const pollOnce = useCallback(async (signal?: AbortSignal) => {
    try {
      const [h, o, hw] = await Promise.all([
        fetchTelemetry<THealth>('/api/health', signal),
        fetchTelemetry<unknown>('/api/overview', signal),
        fetchTelemetry<THardware>('/api/hardware', signal),
      ])
      if (signal?.aborted) return
      applyTick({ health: h, overview: o, hardware: hw })
      setMode((m) => (m === 'ws' ? m : 'poll'))
    } catch (err) {
      if (signal?.aborted) return
      setError(err instanceof Error ? err.message : 'telemetry_error')
      setMode('offline')
    }
  }, [applyTick])

  useEffect(() => {
    let stopped = false
    let retryTimer: number | null = null

    const stopPoll = () => {
      if (pollRef.current != null) {
        window.clearInterval(pollRef.current)
        pollRef.current = null
      }
    }

    const startPoll = () => {
      stopPoll()
      const ctrl = new AbortController()
      void pollOnce(ctrl.signal)
      pollRef.current = window.setInterval(() => {
        void pollOnce()
      }, pollMs)
    }

    const connectWs = () => {
      if (stopped) return
      try {
        const ws = new WebSocket(telemetryWsUrl('/ws/live'))
        wsRef.current = ws
        ws.onopen = () => {
          if (stopped) return
          stopPoll()
          setMode('ws')
          setError(null)
        }
        ws.onmessage = (ev) => {
          try {
            const tick = JSON.parse(String(ev.data)) as LiveTick
            applyTick(tick)
            setMode('ws')
          } catch {
            /* ignore bad frames */
          }
        }
        ws.onerror = () => {
          /* onclose handles fallback */
        }
        ws.onclose = () => {
          if (stopped) return
          setMode('poll')
          startPoll()
          retryTimer = window.setTimeout(connectWs, 4000)
        }
      } catch {
        startPoll()
        retryTimer = window.setTimeout(connectWs, 4000)
      }
    }

    connectWs()

    return () => {
      stopped = true
      stopPoll()
      if (retryTimer != null) window.clearTimeout(retryTimer)
      try {
        wsRef.current?.close()
      } catch {
        /* ignore */
      }
      wsRef.current = null
    }
  }, [applyTick, pollOnce, pollMs])

  return { health, overview, hardware, error, mode, updatedAt }
}

/** Path-based poll (pages that need a single REST endpoint). Default 1s. */
export function useTelemetry<T>(path: string, intervalMs = 1000) {
  const [data, setData] = useState<T | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [updatedAt, setUpdatedAt] = useState<string | null>(null)

  const refresh = useCallback(
    async (signal?: AbortSignal) => {
      try {
        const next = await fetchTelemetry<T>(path, signal)
        if (signal?.aborted) return
        setData(next)
        setError(null)
        setUpdatedAt(new Date().toISOString())
      } catch (err) {
        if (signal?.aborted) return
        setError(err instanceof Error ? err.message : 'telemetry_error')
      }
    },
    [path],
  )

  useEffect(() => {
    const ctrl = new AbortController()
    void refresh(ctrl.signal)
    const id = window.setInterval(() => {
      void refresh()
    }, intervalMs)
    return () => {
      ctrl.abort()
      window.clearInterval(id)
    }
  }, [refresh, intervalMs])

  return { data, error, updatedAt, refresh }
}
