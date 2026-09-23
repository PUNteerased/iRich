# iRich Dashboard (Next.js)

Ops UI for the iRich trading engine. Deploy on [Vercel](https://vercel.com).

## Local

```bash
npm install
npm run dev
```

Set API URL in `.env.local`:

```
NEXT_PUBLIC_TELEMETRY_URL=http://127.0.0.1:8000
```

Run the engine telemetry next to it:

```bash
# from the iRich monorepo root
python scripts/telemetry_api.py
```

## Vercel

1. Import this repo in Vercel (Root Directory = repo root).
2. Framework: Next.js
3. Environment variables:

| Name | Value |
|------|--------|
| `TELEMETRY_UPSTREAM` | Public HTTPS ngrok URL of telemetry API (server proxy) |
| `NEXT_PUBLIC_TELEMETRY_URL` | Same ngrok URL (optional; local/WS only) |

On `*.vercel.app` the browser calls **same-origin** `/api/bridge/*` — the Next.js
route proxies to `TELEMETRY_UPSTREAM` and adds the ngrok skip header server-side.
That avoids CORS / preflight failures with free ngrok.

Redeploy after changing env. Keep `ngrok http 8000` + `telemetry_api` running locally.
