# Ultimate Multi-Pair Sniper ($100 FBS)

Multi-timeframe sniper bot for **EURUSD / USDJPY / XAUUSD** (weekdays) and **BTCUSD** (weekends) on an FBS Standard account. Demo and Real use the **same runtime**; portfolio balance / leverage are read from MT5.

## Hard rules
- **Max total open positions = 1** (entire account)
- **Fixed lot = 0.01** while `money_management.option_a_lock: true`
- **Max risk = $2.00** per trade (skip if SL implies more)
- **Weekday (Mon–Fri):** `EURUSD`, `USDJPY`, `XAUUSD`
- **Weekend (Sat–Sun):** `BTCUSD` only
- **MTF:** Soft-MTF by default (H1 + M1 FVG hard; M15/M5 soft bonuses) + volume confirm
- **ML gate:** `predict_proba >= 0.75`
- **R:R = 1:3** · BE @ 2.0R
- **Monthly circuit breaker:** halt new entries at **−10%** month-to-date

> Profit targets (e.g. ~30%/month) are a **risk-design framework**, not a guarantee.

## Project layout
See `src/`, `scripts/`, `dashboard/`, `notebooks/iRich.ipynb`, `config.yaml`, `iRich_Architecture.md`.

## Setup
```bash
cd "d:\Project\!iRich"
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

Fill `.env` with FBS MT5 login if needed (or use an already-logged-in MT5 terminal).

## Run the bot
```bash
python src\main.py
```

## Ops dashboard (read-only)
Terminal A — telemetry API (port 8000):
```bash
pip install fastapi uvicorn
python scripts\telemetry_api.py
```

Terminal B — Next.js UI (port 3000):
```bash
cd dashboard
pnpm install
pnpm dev
```

Open http://localhost:3000 — polls every 3s. Timestamps display in **ICT (Asia/Bangkok)**.

Use the **account switcher** in the sidebar to add / activate MT5 logins (saved in `data/mt5_accounts.json`, gitignored). Switching account signals a running bot to reconnect automatically.

## 1) Export history from MT5
```bash
python scripts/export_mt5_history.py
```

## 2) Train
Colab: `notebooks/iRich.ipynb` → download `models/*_sniper.pkl`  
Local: `python scripts/weekly_retrain.py`

## Config knobs
Primary file: [`config.yaml`](config.yaml) — Soft-MTF, BE@2R, option_a_lock, session filter, spread calibration.
