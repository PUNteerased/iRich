import { NextRequest, NextResponse } from 'next/server'

export const dynamic = 'force-dynamic'
export const runtime = 'nodejs'

function upstreamBase(): string {
  const raw =
    process.env.TELEMETRY_UPSTREAM ||
    process.env.NEXT_PUBLIC_TELEMETRY_URL ||
    'http://127.0.0.1:8000'
  return raw.replace(/\/$/, '')
}

async function proxy(req: NextRequest, pathParts: string[]) {
  const base = upstreamBase()
  const sub = pathParts.join('/')
  const target = `${base}/${sub}${req.nextUrl.search}`

  const headers: Record<string, string> = {
    Accept: 'application/json',
    // Server-side skip — avoids browser CORS preflight to ngrok.
    'ngrok-skip-browser-warning': '1',
  }
  const ct = req.headers.get('content-type')
  if (ct) headers['Content-Type'] = ct

  let body: ArrayBuffer | undefined
  if (req.method !== 'GET' && req.method !== 'HEAD') {
    body = await req.arrayBuffer()
  }

  let upstream: Response
  try {
    upstream = await fetch(target, {
      method: req.method,
      headers,
      body,
      cache: 'no-store',
      redirect: 'manual',
    })
  } catch (err) {
    const msg = err instanceof Error ? err.message : 'upstream_unreachable'
    return NextResponse.json({ detail: msg, upstream: target }, { status: 502 })
  }

  const outHeaders = new Headers()
  const pass = ['content-type', 'cache-control']
  for (const key of pass) {
    const v = upstream.headers.get(key)
    if (v) outHeaders.set(key, v)
  }
  outHeaders.set('cache-control', 'no-store')

  return new NextResponse(upstream.body, {
    status: upstream.status,
    headers: outHeaders,
  })
}

type Ctx = { params: Promise<{ path: string[] }> }

export async function GET(req: NextRequest, ctx: Ctx) {
  const { path } = await ctx.params
  return proxy(req, path || [])
}

export async function POST(req: NextRequest, ctx: Ctx) {
  const { path } = await ctx.params
  return proxy(req, path || [])
}

export async function PUT(req: NextRequest, ctx: Ctx) {
  const { path } = await ctx.params
  return proxy(req, path || [])
}

export async function DELETE(req: NextRequest, ctx: Ctx) {
  const { path } = await ctx.params
  return proxy(req, path || [])
}
