#!/usr/bin/env bash
set -euo pipefail

APP_NAME="telegram-3xui-shop-bot"
APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="$APP_DIR/.env"
EXAMPLE_ENV="$APP_DIR/.env.example"
PYTHON_BIN="${PYTHON_BIN:-python3}"
SYSTEMD_SERVICE="/etc/systemd/system/${APP_NAME}.service"

cd "$APP_DIR"

say() {
  printf '\n==> %s\n' "$*"
}

warn() {
  printf '\n!! %s\n' "$*"
}

need_command() {
  if ! command -v "$1" >/dev/null 2>&1; then
    warn "Required command not found: $1"
    return 1
  fi
}

ensure_env_file() {
  if [[ ! -f "$ENV_FILE" ]]; then
    if [[ -f "$EXAMPLE_ENV" ]]; then
      cp "$EXAMPLE_ENV" "$ENV_FILE"
    else
      touch "$ENV_FILE"
    fi
    chmod 600 "$ENV_FILE" || true
    say "Created .env"
  fi
}

get_env_value() {
  local key="$1"
  if [[ ! -f "$ENV_FILE" ]]; then
    return 0
  fi
  grep -E "^${key}=" "$ENV_FILE" | tail -n 1 | cut -d= -f2- || true
}

set_env_value() {
  local key="$1"
  local value="$2"
  local escaped
  escaped="$(printf '%s' "$value" | sed 's/[&/\\]/\\&/g')"
  if grep -qE "^${key}=" "$ENV_FILE" 2>/dev/null; then
    sed -i "s/^${key}=.*/${key}=${escaped}/" "$ENV_FILE"
  else
    printf '%s=%s\n' "$key" "$value" >> "$ENV_FILE"
  fi
}

ask() {
  local prompt="$1"
  local default="${2:-}"
  local answer
  if [[ -n "$default" ]]; then
    read -r -p "$prompt [$default]: " answer || true
    printf '%s' "${answer:-$default}"
  else
    read -r -p "$prompt: " answer || true
    printf '%s' "$answer"
  fi
}

ask_secret() {
  local prompt="$1"
  local default="${2:-}"
  local answer
  if [[ -n "$default" ]]; then
    read -r -s -p "$prompt [keep current]: " answer || true
    printf '\n' >&2
    printf '%s' "${answer:-$default}"
  else
    read -r -s -p "$prompt: " answer || true
    printf '\n' >&2
    printf '%s' "$answer"
  fi
}

ask_yes_no() {
  local prompt="$1"
  local default="${2:-n}"
  local answer
  read -r -p "$prompt [$default]: " answer || true
  answer="${answer:-$default}"
  case "${answer,,}" in
    y|yes|1|true|on) return 0 ;;
    *) return 1 ;;
  esac
}

install_system_packages_if_possible() {
  if command -v apt-get >/dev/null 2>&1; then
    if [[ "${EUID:-$(id -u)}" -eq 0 ]]; then
      say "Installing system packages"
      apt-get update
      apt-get install -y python3 python3-venv python3-pip git curl unzip
    else
      warn "Not root; skipping apt packages. If venv creation fails, run: sudo apt-get install python3-venv python3-pip git curl unzip"
    fi
  fi
}

configure_env() {
  ensure_env_file

  say "Configuring .env"

  local current_token token
  current_token="$(get_env_value BOT_TOKEN)"
  if [[ -z "$current_token" || "$current_token" == "PUT_TELEGRAM_BOT_TOKEN_HERE" ]]; then
    token="$(ask_secret 'BOT_TOKEN from BotFather')"
    while [[ -z "$token" || "$token" == "PUT_TELEGRAM_BOT_TOKEN_HERE" ]]; do
      warn "BOT_TOKEN is required. Get it from @BotFather."
      token="$(ask_secret 'BOT_TOKEN from BotFather')"
    done
    set_env_value BOT_TOKEN "$token"
  else
    if ask_yes_no "BOT_TOKEN already exists. Replace it?" "n"; then
      token="$(ask_secret 'New BOT_TOKEN')"
      [[ -n "$token" ]] && set_env_value BOT_TOKEN "$token"
    fi
  fi

  local admin_ids allow_claim
  admin_ids="$(get_env_value ADMIN_IDS)"
  if [[ -z "$admin_ids" ]]; then
    printf '\nTelegram numeric admin ID is recommended. You can get it from @userinfobot or @RawDataBot.\n'
    admin_ids="$(ask 'ADMIN_IDS comma-separated, for example 123456789 or 123,456')"
    if [[ -n "$admin_ids" ]]; then
      set_env_value ADMIN_IDS "$admin_ids"
      set_env_value ALLOW_FIRST_ADMIN_CLAIM "false"
    else
      warn "No ADMIN_IDS provided. First-admin claim is unsafe on public bots."
      if ask_yes_no "Allow the first Telegram user who sends /start to become admin?" "n"; then
        set_env_value ALLOW_FIRST_ADMIN_CLAIM "true"
      else
        set_env_value ALLOW_FIRST_ADMIN_CLAIM "false"
        warn "No admin is configured. Add ADMIN_IDS later or the admin panel will not be accessible."
      fi
    fi
  else
    say "ADMIN_IDS already set: $admin_ids"
    set_env_value ALLOW_FIRST_ADMIN_CLAIM "false"
  fi

  local public_base app_host app_port db_url
  public_base="$(ask 'PUBLIC_BASE_URL for webhooks/API' "$(get_env_value PUBLIC_BASE_URL || true)")"
  [[ -n "$public_base" ]] && set_env_value PUBLIC_BASE_URL "$public_base"

  app_host="$(ask 'APP_HOST' "$(get_env_value APP_HOST || true)")"
  [[ -z "$app_host" ]] && app_host="0.0.0.0"
  set_env_value APP_HOST "$app_host"

  app_port="$(ask 'APP_PORT' "$(get_env_value APP_PORT || true)")"
  [[ -z "$app_port" ]] && app_port="8080"
  set_env_value APP_PORT "$app_port"

  db_url="$(get_env_value DATABASE_URL || true)"
  if [[ -z "$db_url" ]]; then
    set_env_value DATABASE_URL "sqlite+aiosqlite:///./shop.db"
  fi

  if ask_yes_no "Configure 3x-ui connection now?" "n"; then
    local xui_url xui_key xui_header xui_prefix xui_api_prefix xui_sub_url
    xui_url="$(ask 'XUI_BASE_URL, for example https://panel.example.com' "$(get_env_value XUI_BASE_URL || true)")"
    [[ -n "$xui_url" ]] && set_env_value XUI_BASE_URL "$xui_url"
    xui_key="$(ask_secret 'XUI_API_KEY' "$(get_env_value XUI_API_KEY || true)")"
    [[ -n "$xui_key" ]] && set_env_value XUI_API_KEY "$xui_key"
    xui_header="$(ask 'XUI_AUTH_HEADER' "$(get_env_value XUI_AUTH_HEADER || true)")"
    [[ -z "$xui_header" ]] && xui_header="Authorization"
    set_env_value XUI_AUTH_HEADER "$xui_header"
    xui_prefix="$(ask 'XUI_AUTH_PREFIX, use - for empty' "$(get_env_value XUI_AUTH_PREFIX || true)")"
    [[ -z "$xui_prefix" ]] && xui_prefix="Bearer"
    [[ "$xui_prefix" == "-" ]] && xui_prefix=""
    set_env_value XUI_AUTH_PREFIX "$xui_prefix"
    xui_api_prefix="$(ask 'XUI_API_PREFIX' "$(get_env_value XUI_API_PREFIX || true)")"
    [[ -z "$xui_api_prefix" ]] && xui_api_prefix="/panel/api"
    set_env_value XUI_API_PREFIX "$xui_api_prefix"
    xui_sub_url="$(ask 'XUI_SUBSCRIPTION_BASE_URL, empty means same as XUI_BASE_URL' "$(get_env_value XUI_SUBSCRIPTION_BASE_URL || true)")"
    [[ -n "$xui_sub_url" ]] && set_env_value XUI_SUBSCRIPTION_BASE_URL "$xui_sub_url"
  fi

  if ask_yes_no "Configure payments now?" "n"; then
    local now_key now_secret card_number card_owner usd_rate
    usd_rate="$(ask 'USD_RATE_TOMAN' "$(get_env_value USD_RATE_TOMAN || true)")"
    [[ -z "$usd_rate" ]] && usd_rate="150000"
    set_env_value USD_RATE_TOMAN "$usd_rate"

    now_key="$(ask_secret 'NOWPAYMENTS_API_KEY, empty to skip' "$(get_env_value NOWPAYMENTS_API_KEY || true)")"
    [[ -n "$now_key" ]] && set_env_value NOWPAYMENTS_API_KEY "$now_key"
    now_secret="$(ask_secret 'NOWPAYMENTS_IPN_SECRET, empty to skip' "$(get_env_value NOWPAYMENTS_IPN_SECRET || true)")"
    [[ -n "$now_secret" ]] && set_env_value NOWPAYMENTS_IPN_SECRET "$now_secret"

    card_number="$(ask 'CARD_TO_CARD_NUMBER, empty to skip' "$(get_env_value CARD_TO_CARD_NUMBER || true)")"
    [[ -n "$card_number" ]] && set_env_value CARD_TO_CARD_NUMBER "$card_number"
    card_owner="$(ask 'CARD_TO_CARD_OWNER, empty to skip' "$(get_env_value CARD_TO_CARD_OWNER || true)")"
    [[ -n "$card_owner" ]] && set_env_value CARD_TO_CARD_OWNER "$card_owner"
  fi

  chmod 600 "$ENV_FILE" || true
}

setup_python() {
  say "Setting up Python virtualenv"
  need_command "$PYTHON_BIN"
  if [[ ! -d .venv ]]; then
    "$PYTHON_BIN" -m venv .venv
  fi
  # shellcheck disable=SC1091
  source .venv/bin/activate
  python -m pip install --upgrade pip wheel setuptools
  pip install -r requirements.txt
}

init_database() {
  say "Initializing database and runtime defaults"
  # shellcheck disable=SC1091
  source .venv/bin/activate
  python - <<'PY'
import asyncio
from app.runtime_config import ensure_runtime_defaults
from app.config import reload_settings
from app.db import init_db

async def main():
    ensure_runtime_defaults()
    reload_settings()
    await init_db()

asyncio.run(main())
PY
}

write_systemd_service() {
  if [[ "${EUID:-$(id -u)}" -ne 0 ]]; then
    warn "Systemd install needs root. Re-run with sudo or choose manual run."
    return 1
  fi

  local service_user="${SERVICE_USER:-root}"
  if [[ "$APP_DIR" != /root/* && -z "${SERVICE_USER:-}" ]]; then
    service_user="shopbot"
    if ! id "$service_user" >/dev/null 2>&1; then
      useradd --system --home "$APP_DIR" --shell /usr/sbin/nologin "$service_user" || service_user="root"
    fi
    if [[ "$service_user" != "root" ]]; then
      chown -R "$service_user:$service_user" "$APP_DIR" || true
    fi
  fi

  say "Writing systemd service as user: $service_user"
  cat > "$SYSTEMD_SERVICE" <<SERVICE
[Unit]
Description=Telegram 3x-ui Shop Bot
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=${service_user}
WorkingDirectory=${APP_DIR}
Environment=PYTHONUNBUFFERED=1
ExecStart=${APP_DIR}/.venv/bin/python ${APP_DIR}/run.py
Restart=always
RestartSec=5
NoNewPrivileges=true
PrivateTmp=true

[Install]
WantedBy=multi-user.target
SERVICE

  systemctl daemon-reload
  systemctl enable "$APP_NAME"
  systemctl restart "$APP_NAME"
  systemctl --no-pager --full status "$APP_NAME" || true
}

run_manual() {
  say "Starting bot manually"
  # shellcheck disable=SC1091
  source .venv/bin/activate
  exec python run.py
}

print_summary() {
  say "Bootstrap complete"
  printf 'Project: %s\n' "$APP_DIR"
  printf '.env: %s\n' "$ENV_FILE"
  printf '\nUseful commands:\n'
  printf '  Manual run:  source .venv/bin/activate && python run.py\n'
  printf '  Logs systemd: journalctl -u %s -f\n' "$APP_NAME"
  printf '  Restart:      systemctl restart %s\n' "$APP_NAME"
}

main() {
  install_system_packages_if_possible
  configure_env
  setup_python
  init_database

  printf '\nChoose run mode:\n'
  printf '  1) Start manually now\n'
  printf '  2) Install/start systemd service\n'
  printf '  3) Do not start now\n'
  local mode
  mode="$(ask 'Run mode' '2')"
  case "$mode" in
    1) run_manual ;;
    2) write_systemd_service || true ;;
    *) say "Not starting now" ;;
  esac
  print_summary
}

main "$@"
