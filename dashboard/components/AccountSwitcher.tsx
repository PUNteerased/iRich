'use client'

import { useCallback, useEffect, useState } from 'react'
import { Check, ChevronDown, Plus, Trash2, X } from 'lucide-react'
import { fetchTelemetry, mutateTelemetry } from '@/lib/api'

type AccountPublic = {
  id: string
  label: string
  login: number
  server: string
  path: string
  has_password: boolean
}

type AccountsPayload = {
  active_id: string | null
  accounts: AccountPublic[]
}

const emptyForm = {
  label: '',
  login: '',
  password: '',
  server: 'FBS-Demo',
  path: String.raw`C:\Program Files\MetaTrader 5\terminal64.exe`,
}

export function AccountSwitcher() {
  const [open, setOpen] = useState(false)
  const [adding, setAdding] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [data, setData] = useState<AccountsPayload | null>(null)
  const [form, setForm] = useState(emptyForm)

  const refresh = useCallback(async () => {
    try {
      const next = await fetchTelemetry<AccountsPayload>('/api/accounts')
      setData(next)
      setError(null)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'accounts_error')
    }
  }, [])

  useEffect(() => {
    void refresh()
  }, [refresh])

  const active = data?.accounts.find((a) => a.id === data.active_id) || data?.accounts[0]

  async function activate(id: string) {
    setBusy(true)
    setError(null)
    try {
      const res = await mutateTelemetry<{ accounts: AccountsPayload }>(
        `/api/accounts/${id}/activate`,
        'PUT',
      )
      if (res.accounts) setData(res.accounts)
      else await refresh()
      setOpen(false)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'activate_failed')
    } finally {
      setBusy(false)
    }
  }

  async function remove(id: string) {
    if (!window.confirm('ลบบัญชีนี้ออกจากรายการ?')) return
    setBusy(true)
    try {
      await mutateTelemetry(`/api/accounts/${id}`, 'DELETE')
      await refresh()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'delete_failed')
    } finally {
      setBusy(false)
    }
  }

  async function createAccount(e: React.FormEvent) {
    e.preventDefault()
    setBusy(true)
    setError(null)
    try {
      // verify:true is safe — API defers MT5 login while the bot holds the terminal
      await mutateTelemetry('/api/accounts', 'POST', {
        label: form.label || `${form.server} · ${form.login}`,
        login: Number(form.login),
        password: form.password,
        server: form.server,
        path: form.path,
        activate: true,
        verify: true,
      })
      setForm(emptyForm)
      setAdding(false)
      await refresh()
      setOpen(false)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'login_failed')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="workspace-wrap" style={{ position: 'relative' }}>
      <button
        type="button"
        className="workspace"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        style={{ width: '100%', cursor: 'pointer', border: 'none', background: 'inherit', textAlign: 'left' }}
      >
        <span className="workspace-avatar">IR</span>
        <span>
            <b>{active?.label || 'No account'}</b>
            <small>
              {active ? `${active.server} · ${active.login}` : 'Add account'}
            </small>
        </span>
        <ChevronDown size={15} />
      </button>

      {open && (
        <div
          className="panel"
          style={{
            position: 'absolute',
            left: 0,
            right: 0,
            top: 'calc(100% + 6px)',
            zIndex: 40,
            padding: '0.75rem',
            display: 'grid',
            gap: '0.5rem',
            maxHeight: '70vh',
            overflow: 'auto',
          }}
        >
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
            <strong style={{ fontSize: '0.85rem' }}>Trading accounts</strong>
            <button type="button" className="icon-button" onClick={() => setOpen(false)} aria-label="Close">
              <X size={14} />
            </button>
          </div>

          {(data?.accounts || []).map((acc) => {
            const isActive = acc.id === data?.active_id
            return (
              <div
                key={acc.id}
                style={{
                  display: 'flex',
                  gap: '0.4rem',
                  alignItems: 'center',
                  padding: '0.45rem 0.5rem',
                  borderRadius: 8,
                  background: isActive ? 'rgba(16,185,129,0.12)' : 'transparent',
                }}
              >
                <button
                  type="button"
                  className="button button-quiet"
                  style={{ flex: 1, justifyContent: 'flex-start', textAlign: 'left' }}
                  disabled={busy || isActive}
                  onClick={() => void activate(acc.id)}
                >
                  <span>
                    <b>{acc.label}</b>
                    <br />
                    <small className="muted">
                      {acc.server} · {acc.login}
                    </small>
                  </span>
                  {isActive && <Check size={14} />}
                </button>
                <button
                  type="button"
                  className="icon-button"
                  disabled={busy}
                  aria-label="Delete account"
                  onClick={() => void remove(acc.id)}
                >
                  <Trash2 size={14} />
                </button>
              </div>
            )
          })}

          {!adding ? (
            <button type="button" className="button button-primary" onClick={() => setAdding(true)} disabled={busy}>
              <Plus size={14} /> Add account
            </button>
          ) : (
            <form onSubmit={createAccount} style={{ display: 'grid', gap: '0.45rem' }}>
              <input
                className="mono"
                placeholder="Label (optional)"
                value={form.label}
                onChange={(e) => setForm({ ...form, label: e.target.value })}
                style={inputStyle}
              />
              <input
                className="mono"
                placeholder="Login"
                required
                value={form.login}
                onChange={(e) => setForm({ ...form, login: e.target.value })}
                style={inputStyle}
              />
              <input
                className="mono"
                placeholder="Password"
                type="password"
                required
                value={form.password}
                onChange={(e) => setForm({ ...form, password: e.target.value })}
                style={inputStyle}
              />
              <input
                className="mono"
                placeholder="Server (e.g. FBS-Demo)"
                required
                value={form.server}
                onChange={(e) => setForm({ ...form, server: e.target.value })}
                style={inputStyle}
              />
              <input
                className="mono"
                placeholder="MT5 terminal64.exe path"
                value={form.path}
                onChange={(e) => setForm({ ...form, path: e.target.value })}
                style={inputStyle}
              />
              <div style={{ display: 'flex', gap: '0.4rem' }}>
                <button type="submit" className="button button-primary" disabled={busy} style={{ flex: 1 }}>
                  {busy ? 'Logging in…' : 'Login & activate'}
                </button>
                <button type="button" className="button button-quiet" onClick={() => setAdding(false)}>
                  Cancel
                </button>
              </div>
            </form>
          )}

          {error && (
            <p className="negative" style={{ margin: 0, fontSize: '0.75rem' }}>
              {error}
            </p>
          )}
        </div>
      )}
    </div>
  )
}

const inputStyle: React.CSSProperties = {
  width: '100%',
  padding: '0.45rem 0.55rem',
  borderRadius: 8,
  border: '1px solid rgba(255,255,255,0.12)',
  background: 'rgba(0,0,0,0.25)',
  color: 'inherit',
  fontSize: '0.8rem',
}
