"""Telemetry + account-switch API for the iRich Next.js dashboard.

    python scripts/telemetry_api.py

Portfolio / logs endpoints are read-only. Account endpoints may write the local
credentials store (`data/mt5_accounts.json`) and verify MT5 login — they never
place orders.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

from src.accounts import (
    delete_account,
    get_active_account,
    list_accounts_public,
    set_active,
    upsert_account,
    verify_mt5_login,
)
from src.config import load_config
from src.telemetry import (
    build_analytics,
    build_calendar,
    build_config_view,
    build_decisions,
    build_health,
    build_models,
    build_overview,
    build_risk,
    build_trades,
    invalidate_portfolio_cache,
)

app = FastAPI(title="iRich Telemetry", version="1.1.0", docs_url="/docs")

# Local + Vercel. Override with comma list, e.g. CORS_ORIGINS=https://irich-dashboard.vercel.app
_cors = (os.getenv("CORS_ORIGINS") or "*").strip()
_allow_origins = (
    ["*"]
    if _cors == "*"
    else [o.strip() for o in _cors.split(",") if o.strip()]
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=_allow_origins,
    allow_credentials=False,
    allow_methods=["GET", "POST", "PUT", "DELETE"],
    allow_headers=["*"],
)


class AccountBody(BaseModel):
    label: str = ""
    login: int
    password: str = ""
    server: str
    path: str = ""
    activate: bool = True
    verify: bool = True


@app.get("/api/health")
def api_health():
    return build_health(load_config())


@app.get("/api/overview")
def api_overview():
    return build_overview(load_config())


@app.get("/api/decisions")
def api_decisions(limit: int = Query(100, ge=1, le=500)):
    return build_decisions(limit)


@app.get("/api/trades")
def api_trades(limit: int = Query(100, ge=1, le=500)):
    return build_trades(limit, load_config())


@app.get("/api/risk")
def api_risk():
    return build_risk(load_config())


@app.get("/api/models")
def api_models():
    return build_models(load_config())


@app.get("/api/config")
def api_config():
    return build_config_view(load_config())


@app.get("/api/analytics")
def api_analytics():
    return build_analytics()


@app.get("/api/calendar")
def api_calendar(
    year: int | None = None,
    month: int | None = Query(None, ge=1, le=12),
    view: str = Query("month", pattern="^(month|week|year)$"),
    day: str | None = None,
):
    return build_calendar(year=year, month=month, view=view, day=day)


@app.get("/api/accounts")
def api_accounts():
    return list_accounts_public()


@app.post("/api/accounts")
def api_accounts_create(body: AccountBody):
    if not body.password:
        raise HTTPException(status_code=400, detail="password_required")
    if not body.server.strip():
        raise HTTPException(status_code=400, detail="server_required")

    account = {
        "login": body.login,
        "password": body.password,
        "server": body.server.strip(),
        "path": body.path.strip(),
        "label": body.label.strip() or f"{body.server} · {body.login}",
    }
    verify: dict[str, Any] = {"ok": True}
    if body.verify:
        verify = verify_mt5_login(account)
        if not verify.get("ok"):
            raise HTTPException(status_code=400, detail=verify.get("error") or "login_failed")

    result = upsert_account(
        label=account["label"],
        login=account["login"],
        password=account["password"],
        server=account["server"],
        path=account["path"],
        activate=body.activate,
    )
    invalidate_portfolio_cache()
    return {"ok": True, **result, "verify": verify, "accounts": list_accounts_public()}


@app.put("/api/accounts/{account_id}/activate")
def api_accounts_activate(account_id: str):
    store_list = list_accounts_public()["accounts"]
    match = next((a for a in store_list if a["id"] == account_id), None)
    if match is None:
        raise HTTPException(status_code=404, detail="account_not_found")

    # Need full password from store for verify
    from src.accounts import load_store

    full = next((a for a in (load_store().get("accounts") or []) if a.get("id") == account_id), None)
    if full is None:
        raise HTTPException(status_code=404, detail="account_not_found")

    verify = verify_mt5_login(full)
    if not verify.get("ok"):
        raise HTTPException(status_code=400, detail=verify.get("error") or "login_failed")

    set_active(account_id)
    invalidate_portfolio_cache()
    return {"ok": True, "active_id": account_id, "verify": verify, "accounts": list_accounts_public()}


@app.delete("/api/accounts/{account_id}")
def api_accounts_delete(account_id: str):
    try:
        result = delete_account(account_id)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    invalidate_portfolio_cache()
    return {**result, "accounts": list_accounts_public()}


@app.get("/api/accounts/active")
def api_accounts_active():
    acc = get_active_account()
    if acc is None:
        return {"active": None}
    return {
        "active": {
            "id": acc.get("id"),
            "label": acc.get("label"),
            "login": acc.get("login"),
            "server": acc.get("server"),
            "path": acc.get("path") or "",
        }
    }


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8000, reload=False, log_level="info")
