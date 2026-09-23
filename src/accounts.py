"""Multi-account MT5 credentials store (local file, gitignored).

Used by Config + telemetry so the dashboard can switch which broker
login the engine / portfolio snapshot uses. Passwords never leave the
local machine and are omitted from list API responses.
"""

from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .paths import ROOT

STORE_PATH = ROOT / "data" / "mt5_accounts.json"


def _empty_store() -> dict[str, Any]:
    return {"active_id": None, "accounts": [], "updated_at": None}


def load_store() -> dict[str, Any]:
    if not STORE_PATH.exists():
        return _empty_store()
    try:
        raw = json.loads(STORE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return _empty_store()
    if not isinstance(raw, dict):
        return _empty_store()
    raw.setdefault("accounts", [])
    raw.setdefault("active_id", None)
    return raw


def save_store(store: dict[str, Any]) -> None:
    STORE_PATH.parent.mkdir(parents=True, exist_ok=True)
    store = dict(store)
    store["updated_at"] = datetime.now(timezone.utc).isoformat()
    tmp = STORE_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(store, indent=2), encoding="utf-8")
    tmp.replace(STORE_PATH)


def seed_from_env_if_empty() -> dict[str, Any]:
    """Create a default profile from .env / process env when store is empty."""
    store = load_store()
    if store.get("accounts"):
        return store
    login = os.getenv("MT5_LOGIN")
    password = os.getenv("MT5_PASSWORD")
    server = os.getenv("MT5_SERVER")
    path = os.getenv("MT5_PATH")
    if not login or not password or not server:
        return store
    account = {
        "id": str(uuid.uuid4()),
        "label": f"{server} - {login}",
        "login": int(login),
        "password": password,
        "server": server,
        "path": path or "",
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    store["accounts"] = [account]
    store["active_id"] = account["id"]
    save_store(store)
    return store


def list_accounts_public() -> dict[str, Any]:
    store = seed_from_env_if_empty()
    public = []
    for acc in store.get("accounts") or []:
        public.append(
            {
                "id": acc.get("id"),
                "label": acc.get("label"),
                "login": acc.get("login"),
                "server": acc.get("server"),
                "path": acc.get("path") or "",
                "has_password": bool(acc.get("password")),
            }
        )
    return {
        "active_id": store.get("active_id"),
        "accounts": public,
        "updated_at": store.get("updated_at"),
    }


def upsert_account(
    *,
    label: str,
    login: int,
    password: str,
    server: str,
    path: str = "",
    account_id: str | None = None,
    activate: bool = True,
) -> dict[str, Any]:
    store = load_store()
    accounts: list[dict[str, Any]] = list(store.get("accounts") or [])
    if account_id:
        found = False
        for acc in accounts:
            if acc.get("id") == account_id:
                acc["label"] = label.strip() or acc.get("label")
                acc["login"] = int(login)
                if password:
                    acc["password"] = password
                acc["server"] = server.strip()
                acc["path"] = (path or "").strip()
                found = True
                break
        if not found:
            raise ValueError("account_not_found")
        target_id = account_id
    else:
        target_id = str(uuid.uuid4())
        accounts.append(
            {
                "id": target_id,
                "label": label.strip() or f"{server} - {login}",
                "login": int(login),
                "password": password,
                "server": server.strip(),
                "path": (path or "").strip(),
                "created_at": datetime.now(timezone.utc).isoformat(),
            }
        )
    store["accounts"] = accounts
    if activate:
        store["active_id"] = target_id
    save_store(store)
    if activate:
        request_mt5_reload()
    return {"id": target_id, "active_id": store.get("active_id")}


def delete_account(account_id: str) -> dict[str, Any]:
    store = load_store()
    accounts = [a for a in (store.get("accounts") or []) if a.get("id") != account_id]
    store["accounts"] = accounts
    if store.get("active_id") == account_id:
        store["active_id"] = accounts[0]["id"] if accounts else None
    save_store(store)
    return {"ok": True, "active_id": store.get("active_id")}


def set_active(account_id: str) -> dict[str, Any]:
    store = load_store()
    if not any(a.get("id") == account_id for a in (store.get("accounts") or [])):
        raise ValueError("account_not_found")
    store["active_id"] = account_id
    save_store(store)
    request_mt5_reload()
    return {"ok": True, "active_id": account_id}


def request_mt5_reload() -> None:
    """Signal a running bot to reconnect MT5 with the active account."""
    from .paths import state_dir

    path = state_dir() / "reload_mt5.flag"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("1", encoding="utf-8")


def consume_mt5_reload_flag() -> bool:
    from .paths import state_dir

    path = state_dir() / "reload_mt5.flag"
    if not path.exists():
        return False
    try:
        path.unlink()
    except OSError:
        return False
    return True


def get_active_account() -> dict[str, Any] | None:
    store = seed_from_env_if_empty()
    active_id = store.get("active_id")
    for acc in store.get("accounts") or []:
        if acc.get("id") == active_id:
            return acc
    accounts = store.get("accounts") or []
    return accounts[0] if accounts else None


def apply_active_account(mt5_cfg: dict[str, Any]) -> dict[str, Any]:
    """Overlay active saved account onto config mt5 dict (mutates and returns)."""
    acc = get_active_account()
    if acc is None:
        return mt5_cfg
    mt5_cfg["login"] = int(acc["login"])
    mt5_cfg["password"] = acc.get("password")
    mt5_cfg["server"] = acc.get("server")
    if acc.get("path"):
        mt5_cfg["path"] = acc["path"]
    return mt5_cfg


def bot_holds_mt5(max_age_sec: float = 60.0) -> bool:
    """True when the trading bot recently published a portfolio snapshot.

    On Windows the MetaTrader5 binding is effectively single-owner. While the
    bot holds the terminal, the telemetry API must NOT call initialize/shutdown.
    """
    from .portfolio_snapshot import read_portfolio_snapshot

    return read_portfolio_snapshot(max_age_sec=max_age_sec) is not None


def verify_mt5_login(account: dict[str, Any], *, force: bool = False) -> dict[str, Any]:
    """Attempt a one-shot MT5 login; always shutdown afterward.

    If the bot already holds MT5 (fresh portfolio snapshot), skip the live
    initialize and return deferred=ok so credentials can be saved + reload flag
    written without knocking the engine offline (avoids IPC timeout -10005).
    """
    if not force and bot_holds_mt5():
        return {
            "ok": True,
            "deferred": True,
            "reason": "bot_holds_mt5",
            "login": int(account.get("login") or 0),
            "server": str(account.get("server") or ""),
        }

    try:
        import MetaTrader5 as mt5
    except ImportError as exc:
        return {"ok": False, "error": f"MetaTrader5 unavailable: {exc}"}

    kwargs: dict[str, Any] = {
        "login": int(account["login"]),
        "password": str(account["password"]),
        "server": str(account["server"]),
    }
    if account.get("path"):
        kwargs["path"] = str(account["path"])

    try:
        mt5.shutdown()
    except Exception:  # noqa: BLE001
        pass

    ok = mt5.initialize(**kwargs)
    if not ok:
        err = mt5.last_error()
        return {"ok": False, "error": f"initialize_failed: {err}"}
    info = mt5.account_info()
    if info is None:
        err = mt5.last_error()
        mt5.shutdown()
        return {"ok": False, "error": f"no_account_info: {err}"}
    result = {
        "ok": True,
        "deferred": False,
        "login": int(info.login),
        "server": str(info.server),
        "balance": float(info.balance),
        "equity": float(info.equity),
        "leverage": int(info.leverage),
        "is_demo": int(info.trade_mode) != int(getattr(mt5, "ACCOUNT_TRADE_MODE_REAL", 2)),
    }
    mt5.shutdown()
    return result
