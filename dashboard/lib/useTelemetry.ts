'use client'

import { useCallback, useEffect, useState } from 'react'
import { fetchTelemetry } from './api'

export function useTelemetry<T>(path: string, intervalMs = 3000) {
  const [data, setData] = useState<T | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [updatedAt, setUpdatedAt] = useState<string | null>(null)

  const refresh = useCallback(async (signal?: AbortSignal) => {
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
  }, [path])

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
