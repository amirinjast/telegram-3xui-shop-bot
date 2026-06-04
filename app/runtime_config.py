from __future__ import annotations

import json
import os
import secrets
import sqlite3
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse


def _db_path_from_url(database_url: str | None = None) -> Path:
    """Return the local SQLite database path used by the async SQLAlchemy URL.

    The bot is designed to be runnable with only BOT_TOKEN in .env, so the
    default database is ./shop.db. Runtime settings are stored in the same
    SQLite database, in app_settings.
    """
    url = database_url or os.getenv("DATABASE_URL") or "sqlite+aiosqlite:///./shop.db"
    if url.startswith("sqlite+aiosqlite:///"):
        raw = url.replace("sqlite+aiosqlite:///", "", 1)
    elif url.startswith("sqlite:///"):
        raw = url.replace("sqlite:///", "", 1)
    else:
        # This project currently expects SQLite for runtime bot settings.
        raw = "./shop.db"
    raw = unquote(raw)
    if raw in {":memory:", "/:memory:"}:
        return Path("shop.db")
    return Path(raw)


def _connect() -> sqlite3.Connection:
    path = _db_path_from_url()
    if path.parent and str(path.parent) not in {"", "."}:
        path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def ensure_settings_table() -> None:
    with _connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS app_settings (
                key TEXT PRIMARY KEY,
                value_json TEXT NOT NULL,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        conn.commit()


def load_runtime_config(path: str | None = None) -> dict[str, Any]:
    # path is kept for backward compatibility with older project code.
    del path
    try:
        ensure_settings_table()
        with _connect() as conn:
            rows = conn.execute("SELECT key, value_json FROM app_settings").fetchall()
    except Exception:
        return {}

    data: dict[str, Any] = {}
    for row in rows:
        try:
            data[row["key"]] = json.loads(row["value_json"])
        except Exception:
            data[row["key"]] = row["value_json"]
    return data


def save_runtime_config(data: dict[str, Any], path: str | None = None) -> None:
    del path
    ensure_settings_table()
    with _connect() as conn:
        conn.execute("DELETE FROM app_settings")
        conn.executemany(
            "INSERT INTO app_settings(key, value_json, updated_at) VALUES(?, ?, CURRENT_TIMESTAMP)",
            [(key, json.dumps(value, ensure_ascii=False)) for key, value in data.items()],
        )
        conn.commit()


def update_runtime_config(values: dict[str, Any], path: str | None = None) -> dict[str, Any]:
    del path
    ensure_settings_table()
    with _connect() as conn:
        for key, value in values.items():
            conn.execute(
                """
                INSERT INTO app_settings(key, value_json, updated_at)
                VALUES(?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(key) DO UPDATE SET
                    value_json = excluded.value_json,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (key, json.dumps(value, ensure_ascii=False)),
            )
        conn.commit()
    return load_runtime_config()


def ensure_runtime_defaults(path: str | None = None) -> dict[str, Any]:
    del path
    ensure_settings_table()
    data = load_runtime_config()
    defaults: dict[str, Any] = {
        "INTERNAL_API_KEY": secrets.token_hex(32),
        "LOCAL_CURRENCY_UNIT": "toman",
        "USD_RATE_TOMAN": 150000,
        "MIN_TOPUP_TOMAN": 50000,
        "NOWPAYMENTS_ENABLED": True,
        "NOWPAYMENTS_BASE_URL": "https://api.nowpayments.io/v1",
        "NOWPAYMENTS_PRICE_CURRENCY": "usd",
        "NOWPAYMENTS_SUCCESS_STATUSES": "finished,confirmed",
        "MANUAL_PAYMENT_ENABLED": True,
        "XUI_AUTH_HEADER": "Authorization",
        "XUI_AUTH_PREFIX": "Bearer",
        "XUI_API_PREFIX": "/panel/api",
        "XUI_DEFAULT_INBOUND_ID": 1,
        "XUI_DEFAULT_LIMIT_IP": 0,
        "XUI_DEFAULT_FLOW": "",
        "PUBLIC_BASE_URL": "http://localhost:8080",
        "TRIAL_ENABLED": False,
        "EXPIRE_WARNING_CHECK_HOURS": 6,
    }
    missing = {key: value for key, value in defaults.items() if key not in data or data.get(key) in {None}}
    if missing:
        update_runtime_config(missing)
    return load_runtime_config()


def mask_value(value: Any, *, keep: int = 4) -> str:
    text = "" if value is None else str(value)
    if not text:
        return "تنظیم نشده"
    if len(text) <= keep * 2:
        return "*" * len(text)
    return text[:keep] + "…" + text[-keep:]
