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
3. Environment variable:

| Name | Value |
|------|--------|
| `NEXT_PUBLIC_TELEMETRY_URL` | Public HTTPS URL of your telemetry API |

The dashboard is read-mostly UI. Portfolio/logs come from the Python `telemetry_api` service — host that API on a VPS/cloud that can reach your MT5 machine (or the same PC with a tunnel).
