#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import re
import secrets
import shutil
import sqlite3
import ssl
import subprocess
import sys
from getpass import getpass
from http.cookiejar import CookieJar
from pathlib import Path
from urllib import error, parse, request

APP_NAME = "telegram-3xui-shop-bot"
ROOT = Path(__file__).resolve().parent
ENV_FILE = ROOT / ".env"
ENV_EXAMPLE = ROOT / ".env.example"
SERVICE_FILE = Path(f"/etc/systemd/system/{APP_NAME}.service")


def say(text: str) -> None:
    print(f"\n==> {text}")


def warn(text: str) -> None:
    print(f"\n!! {text}")


def ask(prompt: str, default: str = "") -> str:
    suffix = f" [{default}]" if default else ""
    value = input(f"{prompt}{suffix}: ").strip()
    return value or default


def ask_secret(prompt: str, default: str = "") -> str:
    suffix = " [keep current]" if default else ""
    value = getpass(f"{prompt}{suffix}: ").strip()
    return value or default


def yes_no(prompt: str, default: bool = False) -> bool:
    d = "y" if default else "n"
    value = input(f"{prompt} [{d}]: ").strip().lower() or d
    return value in {"y", "yes", "1", "true", "on"}


def run(cmd: list[str], *, check: bool = True) -> subprocess.CompletedProcess:
    print("+ " + " ".join(cmd))
    return subprocess.run(cmd, cwd=ROOT, check=check)


def ensure_env() -> None:
    if not ENV_FILE.exists():
        if ENV_EXAMPLE.exists():
            ENV_FILE.write_text(ENV_EXAMPLE.read_text(encoding="utf-8"), encoding="utf-8")
        else:
            ENV_FILE.write_text("", encoding="utf-8")
        try:
            ENV_FILE.chmod(0o600)
        except OSError:
            pass


def read_env() -> dict[str, str]:
    ensure_env()
    data: dict[str, str] = {}
    for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
        if not line or line.lstrip().startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        data[key.strip()] = value.strip()
    return data


def write_env(updates: dict[str, object]) -> None:
    ensure_env()
    lines = ENV_FILE.read_text(encoding="utf-8").splitlines()
    seen: set[str] = set()
    out: list[str] = []
    for line in lines:
        if "=" not in line or line.lstrip().startswith("#"):
            out.append(line)
            continue
        key = line.split("=", 1)[0].strip()
        if key in updates:
            out.append(f"{key}={updates[key]}")
            seen.add(key)
        else:
            out.append(line)
    for key, value in updates.items():
        if key not in seen:
            out.append(f"{key}={value}")
    ENV_FILE.write_text("\n".join(out).rstrip() + "\n", encoding="utf-8")
    try:
        ENV_FILE.chmod(0o600)
    except OSError:
        pass


def db_path_from_env(env: dict[str, str]) -> Path:
    url = env.get("DATABASE_URL") or "sqlite+aiosqlite:///./shop.db"
    if url.startswith("sqlite+aiosqlite:///"):
        raw = url.replace("sqlite+aiosqlite:///", "", 1)
    elif url.startswith("sqlite:///"):
        raw = url.replace("sqlite:///", "", 1)
    else:
        raw = "./shop.db"
    raw = parse.unquote(raw)
    path = Path(raw)
    return path if path.is_absolute() else ROOT / path


def write_runtime_settings(env: dict[str, str], updates: dict[str, object]) -> None:
    db_path = db_path_from_env(env | {k: str(v) for k, v in updates.items()})
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS app_settings (
                key TEXT PRIMARY KEY,
                value_json TEXT NOT NULL,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        for key, value in updates.items():
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


def mask(value: str, keep: int = 4) -> str:
    if not value:
        return ""
    if len(value) <= keep * 2:
        return "*" * len(value)
    return value[:keep] + "…" + value[-keep:]


def host_from_url(value: str) -> str:
    value = value.strip()
    value = re.sub(r"^https?://", "", value)
    value = value.split("/", 1)[0]
    return value.split(":", 1)[0]


def strip_port_url(value: str) -> str:
    m = re.match(r"^(https?://)([^/:]+)(?::\d+)?(/.*)?$", value.strip())
    if not m:
        return value.rstrip("/")
    return (m.group(1) + m.group(2) + (m.group(3) or "")).rstrip("/")


def root_domain(host: str) -> str:
    parts = host.split(".")
    if len(parts) >= 2:
        return ".".join(parts[-2:])
    return host


class JsonHttp:
    def __init__(self) -> None:
        self.cookies = CookieJar()
        ctx = ssl._create_unverified_context()
        self.opener = request.build_opener(request.HTTPCookieProcessor(self.cookies), request.HTTPSHandler(context=ctx))

    def json(self, method: str, url: str, body: object | None = None, headers: dict[str, str] | None = None) -> tuple[int, object | None, str]:
        data = None
        req_headers = {"Accept": "application/json"}
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            req_headers["Content-Type"] = "application/json"
        if headers:
            req_headers.update(headers)
        req = request.Request(url, data=data, headers=req_headers, method=method.upper())
        try:
            with self.opener.open(req, timeout=25) as resp:
                text = resp.read().decode("utf-8", "replace")
                try:
                    return resp.status, json.loads(text), text
                except json.JSONDecodeError:
                    return resp.status, None, text
        except error.HTTPError as exc:
            text = exc.read().decode("utf-8", "replace")
            try:
                return exc.code, json.loads(text), text
            except json.JSONDecodeError:
                return exc.code, None, text
        except Exception as exc:
            return 0, None, str(exc)


def join_url(base: str, path: str) -> str:
    return base.rstrip("/") + "/" + path.strip("/")


def api_url(base: str, prefix: str, path: str) -> str:
    base = base.rstrip("/")
    prefix = prefix.strip().strip("/")
    path = path.strip("/")
    if not prefix:
        return join_url(base, path)
    if base.lower().endswith("/" + prefix.lower()):
        return join_url(base, path)
    if prefix.lower().endswith("/api"):
        no_api = prefix[:-4].rstrip("/")
        if no_api and base.lower().endswith("/" + no_api.lower()):
            return join_url(base, "api/" + path)
    return join_url(base, prefix + "/" + path)


def panel_url(base: str, path: str) -> str:
    base = base.rstrip("/")
    path = path.strip("/")
    if path.startswith("panel/") and base.lower().endswith("/panel"):
        path = path[len("panel/"):]
    return join_url(base, path)


def api_prefix_candidates(prefix: str) -> list[str]:
    candidates: list[str] = []
    for item in (prefix, "panel/api", "api", "xui/API", "xui/api", ""):
        item = item.strip().strip("/")
        if item not in candidates:
            candidates.append(item)
    return candidates


def test_telegram(bot_token: str) -> bool:
    if not bot_token:
        warn("BOT_TOKEN خالی است.")
        return False
    http = JsonHttp()
    status, data, text = http.json("GET", f"https://api.telegram.org/bot{bot_token}/getMe")
    ok = status == 200 and isinstance(data, dict) and data.get("ok") is True
    if ok:
        user = data.get("result") or {}
        print(f"✅ Telegram OK: @{user.get('username', 'unknown')}")
        return True
    warn(f"Telegram test failed: HTTP {status} {text[:300]}")
    return False


def xui_headers(api_key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {api_key}"}


def fetch_inbounds(base: str, api_key: str, prefix: str = "/panel/api") -> tuple[list[dict], list[str]]:
    http = JsonHttp()
    errors: list[str] = []
    for api_prefix in api_prefix_candidates(prefix):
        for path in ("inbounds/options", "inbounds/list/slim", "inbounds/list"):
            url = api_url(base, api_prefix, path)
            status, data, text = http.json("GET", url, headers=xui_headers(api_key))
            if status != 200:
                errors.append(f"{url} -> HTTP {status}: {text[:180]}")
                continue
            if not isinstance(data, dict) or data.get("success") is False:
                errors.append(f"{url} -> bad JSON: {text[:180]}")
                continue
            obj = data.get("obj") or data.get("data") or data.get("inbounds")
            if isinstance(obj, list):
                return [x for x in obj if isinstance(x, dict)], errors
            errors.append(f"{url} -> no inbound list in obj")
    return [], errors


def login_and_create_xui_api_key(base: str, username: str, panel_pass: str, two_factor_code: str = "") -> str:
    http = JsonHttp()
    login_url = join_url(base, "login")
    body = {"username": username, "password": panel_pass, "twoFactorCode": two_factor_code or ""}
    status, data, text = http.json("POST", login_url, body=body)
    if status != 200 or not isinstance(data, dict) or data.get("success") is False:
        raise RuntimeError(f"Login failed: HTTP {status} {text[:300]}")

    create_url = panel_url(base, "panel/setting/apiTokens/create")
    token_name = "telegram-shop-bot-" + secrets.token_hex(3)
    status, data, text = http.json("POST", create_url, body={"name": token_name})
    if status != 200 or not isinstance(data, dict) or data.get("success") is False:
        raise RuntimeError(f"Could not create API token: HTTP {status} {text[:300]}")
    obj = data.get("obj") or {}
    api_key = obj.get("token") if isinstance(obj, dict) else ""
    if not api_key:
        raise RuntimeError("API token create endpoint did not return obj.token")
    print(f"✅ 3x-ui API token created: {token_name}")
    return api_key


def configure_xui(env: dict[str, str]) -> dict[str, object]:
    say("3x-ui setup")
    print("برای سورس اصلی MHSanaei/3x-ui، بهترین auth برای بات این است: Authorization: Bearer API_TOKEN")
    xui_base = ask("XUI_BASE_URL, مثلا https://panel.example.com:25567", env.get("XUI_BASE_URL", "")).rstrip("/")
    prefix = ask("XUI_API_PREFIX", env.get("XUI_API_PREFIX", "/panel/api") or "/panel/api")

    api_key = ask_secret("XUI API Token from Settings > Security, empty to auto-create with panel login", env.get("XUI_API_KEY", ""))
    if not api_key:
        username = ask("3x-ui admin username")
        panel_pass = ask_secret("3x-ui admin password")
        twofa = ask("2FA code, empty if disabled")
        api_key = login_and_create_xui_api_key(xui_base, username, panel_pass, twofa)

    inbounds, errors = fetch_inbounds(xui_base, api_key, prefix)
    if not inbounds:
        warn("اتصال به 3x-ui برقرار نشد یا هیچ inbound پیدا نشد.")
        for item in errors[-8:]:
            print("- " + item)
        raise SystemExit(1)

    print(f"✅ 3x-ui OK. Found {len(inbounds)} inbound(s):")
    for ib in inbounds[:20]:
        print(f"  id={ib.get('id')} | {ib.get('remark') or ib.get('tag') or ib.get('protocol')} | port={ib.get('port', '-')}")

    current_default = env.get("XUI_DEFAULT_INBOUND_ID", "") or str(inbounds[0].get("id") or 1)
    default_inbound = ask("Default inbound id for sales", current_default)
    sub_base_default = strip_port_url(env.get("XUI_SUBSCRIPTION_BASE_URL") or xui_base)
    sub_base = ask("Public subscription base URL without port", sub_base_default).rstrip("/")
    sub_template = ask("Subscription path template", env.get("XUI_SUBSCRIPTION_PATH_TEMPLATE", "/sub/{sub_id}"))

    return {
        "XUI_BASE_URL": xui_base,
        "XUI_API_KEY": api_key,
        "XUI_AUTH_HEADER": "Authorization",
        "XUI_AUTH_PREFIX": "Bearer",
        "XUI_API_PREFIX": prefix,
        "XUI_DEFAULT_INBOUND_ID": int(default_inbound),
        "XUI_SUBSCRIPTION_BASE_URL": sub_base,
        "XUI_SUBSCRIPTION_PATH_TEMPLATE": sub_template,
    }


def write_nginx_site(domain: str, upstream: str, cert_path: str, key_path: str, name: str) -> None:
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", f"{APP_NAME}-{name}-{domain}")
    available = Path("/etc/nginx/sites-available") / f"{safe}.conf"
    enabled = Path("/etc/nginx/sites-enabled") / f"{safe}.conf"
    conf = f"""
server {{
    listen 443 ssl http2;
    server_name {domain};

    ssl_certificate {cert_path};
    ssl_certificate_key {key_path};

    client_max_body_size 20m;

    location / {{
        proxy_pass {upstream};
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto https;
    }}
}}

server {{
    listen 80;
    server_name {domain};
    return 301 https://$host$request_uri;
}}
""".strip() + "\n"
    available.write_text(conf, encoding="utf-8")
    if enabled.exists() or enabled.is_symlink():
        enabled.unlink()
    enabled.symlink_to(available)
    print(f"✅ nginx: https://{domain} -> {upstream}")


def configure_nginx(env: dict[str, str], updates: dict[str, object]) -> None:
    if os.geteuid() != 0:
        warn("برای تنظیم nginx باید setup.py را با sudo اجرا کنی. فعلاً nginx رد شد.")
        return
    if not yes_no("Configure nginx reverse proxy?", True):
        return

    public_base = str(updates.get("PUBLIC_BASE_URL") or env.get("PUBLIC_BASE_URL") or "")
    bot_domain = host_from_url(public_base) or ask("Bot domain for webhook, e.g. bot.example.com")
    if not bot_domain:
        warn("Bot domain خالی بود، nginx رد شد.")
        return

    cert_default = env.get("NGINX_SSL_CERT") or f"/root/cert/{root_domain(bot_domain)}/fullchain.pem"
    key_default = env.get("NGINX_SSL_KEY") or f"/root/cert/{root_domain(bot_domain)}/privkey.pem"
    cert_path = ask("SSL fullchain path", cert_default)
    key_path = ask("SSL privkey path", key_default)
    if not Path(cert_path).exists() or not Path(key_path).exists():
        warn("فایل SSL پیدا نشد، nginx رد شد.")
        return

    app_port = int(str(updates.get("APP_PORT") or env.get("APP_PORT") or "8080"))
    write_nginx_site(bot_domain, f"http://127.0.0.1:{app_port}", cert_path, key_path, "bot")
    updates["PUBLIC_BASE_URL"] = f"https://{bot_domain}"
    updates["APP_HOST"] = "127.0.0.1"
    updates["NGINX_SSL_CERT"] = cert_path
    updates["NGINX_SSL_KEY"] = key_path

    if yes_no("Configure subscription domain proxy without port?", False):
        sub_base = str(updates.get("XUI_SUBSCRIPTION_BASE_URL") or env.get("XUI_SUBSCRIPTION_BASE_URL") or "")
        sub_domain = ask("Subscription public domain", host_from_url(sub_base))
        xui_base = str(updates.get("XUI_BASE_URL") or env.get("XUI_BASE_URL") or "")
        upstream = ask("Subscription upstream with real port", xui_base)
        if sub_domain and upstream:
            write_nginx_site(sub_domain, upstream, cert_path, key_path, "sub")
            updates["XUI_SUBSCRIPTION_BASE_URL"] = f"https://{sub_domain}"
            updates["NGINX_SUBSCRIPTION_PROXY"] = "true"
            updates["NGINX_SUBSCRIPTION_PROXY_TARGET"] = upstream

    run(["nginx", "-t"])
    run(["systemctl", "enable", "--now", "nginx"])
    run(["systemctl", "reload", "nginx"])


def install_and_service() -> None:
    if os.geteuid() == 0 and shutil.which("apt-get") and yes_no("Install system packages with apt?", True):
        run(["apt-get", "update"])
        run(["apt-get", "install", "-y", "python3", "python3-venv", "python3-pip", "git", "curl", "unzip", "nginx"])

    say("Python virtualenv")
    run([sys.executable, "-m", "venv", ".venv"])
    pip = str(ROOT / ".venv" / "bin" / "pip")
    py = str(ROOT / ".venv" / "bin" / "python")
    run([py, "-m", "pip", "install", "--upgrade", "pip", "wheel", "setuptools"])
    run([pip, "install", "-r", "requirements.txt"])

    say("Database init")
    run([py, "-c", "import asyncio; from app.runtime_config import ensure_runtime_defaults; from app.config import reload_settings; from app.db import init_db; ensure_runtime_defaults(); reload_settings(); asyncio.run(init_db())"])

    if os.geteuid() == 0:
        say("systemd service")
        SERVICE_FILE.write_text(f"""
[Unit]
Description=Telegram 3x-ui Shop Bot
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory={ROOT}
Environment=PYTHONUNBUFFERED=1
ExecStart={ROOT}/.venv/bin/python {ROOT}/run.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
""".lstrip(), encoding="utf-8")
        run(["systemctl", "daemon-reload"])
        run(["systemctl", "enable", "--now", APP_NAME])
    else:
        warn("برای systemd باید با sudo اجرا شود؛ نصب venv انجام شد ولی سرویس ساخته نشد.")


def main() -> None:
    os.chdir(ROOT)
    env = read_env()
    updates: dict[str, object] = {}

    say("Telegram bot")
    bot_token = ask_secret("BOT_TOKEN from BotFather", env.get("BOT_TOKEN", "") if env.get("BOT_TOKEN") != "PUT_TELEGRAM_BOT_TOKEN_HERE" else "")
    if bot_token:
        test_telegram(bot_token)
        updates["BOT_TOKEN"] = bot_token

    admin_ids = ask("ADMIN_IDS comma-separated, empty to keep/skip", env.get("ADMIN_IDS", ""))
    if admin_ids:
        updates["ADMIN_IDS"] = admin_ids
        updates["ALLOW_FIRST_ADMIN_CLAIM"] = "false"
    elif not env.get("ADMIN_IDS"):
        updates["ALLOW_FIRST_ADMIN_CLAIM"] = "true"

    updates["APP_HOST"] = env.get("APP_HOST", "127.0.0.1") or "127.0.0.1"
    updates["APP_PORT"] = int(env.get("APP_PORT", "8080") or "8080")
    public_base = ask("PUBLIC_BASE_URL for bot/API/webhook", env.get("PUBLIC_BASE_URL", "https://bot.example.com"))
    if public_base:
        updates["PUBLIC_BASE_URL"] = public_base.rstrip("/")

    if yes_no("Configure 3x-ui now?", True):
        updates.update(configure_xui(env))

    if yes_no("Configure NOWPayments?", bool(env.get("NOWPAYMENTS_API_KEY"))):
        now_key = ask_secret("NOWPAYMENTS_API_KEY", env.get("NOWPAYMENTS_API_KEY", ""))
        if now_key:
            updates["NOWPAYMENTS_API_KEY"] = now_key
        updates["NOWPAYMENTS_IPN_SECRET"] = ask_secret("NOWPAYMENTS_IPN_SECRET optional", env.get("NOWPAYMENTS_IPN_SECRET", ""))

    usd_rate = ask("USD_RATE_TOMAN", env.get("USD_RATE_TOMAN", "150000"))
    if usd_rate.isdigit():
        updates["USD_RATE_TOMAN"] = int(usd_rate)

    configure_nginx(env, updates)
    write_env(updates)
    write_runtime_settings(env, updates)
    print("\n✅ .env and runtime SQLite settings updated.")

    if yes_no("Install dependencies and start systemd service now?", True):
        install_and_service()

    final_env = read_env()
    print("\n✅ Setup complete")
    print(f"Health: curl {final_env.get('PUBLIC_BASE_URL', '')}/health")
    print(f"Service status: systemctl status {APP_NAME} --no-pager")
    print(f"Logs: journalctl -u {APP_NAME} -f")
    if final_env.get("XUI_API_KEY"):
        print(f"XUI token saved: {mask(final_env['XUI_API_KEY'])}")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nCancelled.")
        raise SystemExit(130)
