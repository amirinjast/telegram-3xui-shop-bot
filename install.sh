#!/usr/bin/env bash
set -euo pipefail

APP_NAME="telegram-3xui-shop-bot"
APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON_BIN="${PYTHON_BIN:-python3}"
SERVICE_FILE="/etc/systemd/system/${APP_NAME}.service"
ENV_FILE="$APP_DIR/.env"
EXAMPLE_ENV="$APP_DIR/.env.example"

if [[ "${EUID}" -ne 0 ]]; then
  echo "Please run as root: sudo bash install.sh"
  exit 1
fi

say() {
  printf '\n==> %s\n' "$*"
}

warn() {
  printf '\n!! %s\n' "$*"
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

url_host() {
  local value="$1"
  value="${value#http://}"
  value="${value#https://}"
  value="${value%%/*}"
  value="${value%%:*}"
  printf '%s' "$value"
}

root_domain_from_host() {
  local host="$1"
  local count
  IFS='.' read -r -a parts <<< "$host"
  count="${#parts[@]}"
  if (( count >= 2 )); then
    printf '%s.%s' "${parts[count-2]}" "${parts[count-1]}"
  else
    printf '%s' "$host"
  fi
}

sanitize_site_name() {
  printf '%s' "$1" | tr -c 'A-Za-z0-9_.-' '_'
}

ensure_env_file() {
  if [[ ! -f "$ENV_FILE" ]]; then
    if [[ -f "$EXAMPLE_ENV" ]]; then
      cp "$EXAMPLE_ENV" "$ENV_FILE"
    else
      touch "$ENV_FILE"
    fi
    chmod 600 "$ENV_FILE" || true
    echo "==> Created .env from .env.example"
  fi
}

configure_env_interactive() {
  ensure_env_file

  local current_token token
  current_token="$(get_env_value BOT_TOKEN || true)"
  if [[ -t 0 && ( -z "$current_token" || "$current_token" == "PUT_TELEGRAM_BOT_TOKEN_HERE" ) ]]; then
    token="$(ask_secret 'BOT_TOKEN from BotFather, empty to edit later')"
    if [[ -n "$token" ]]; then
      set_env_value BOT_TOKEN "$token"
    fi
  fi

  local now_key
  now_key="$(get_env_value NOWPAYMENTS_API_KEY || true)"
  if [[ -t 0 && -z "$now_key" ]]; then
    now_key="$(ask_secret 'NOWPAYMENTS_API_KEY, empty to skip')"
    if [[ -n "$now_key" ]]; then
      set_env_value NOWPAYMENTS_API_KEY "$now_key"
    fi
  fi
}

write_nginx_proxy_site() {
  local domain="$1"
  local upstream="$2"
  local cert_path="$3"
  local key_path="$4"
  local site_suffix="$5"
  local site_name
  site_name="$(sanitize_site_name "${APP_NAME}-${site_suffix}-${domain}")"

  cat > "/etc/nginx/sites-available/${site_name}.conf" <<NGINX
server {
    listen 443 ssl http2;
    server_name ${domain};

    ssl_certificate ${cert_path};
    ssl_certificate_key ${key_path};

    client_max_body_size 20m;

    location / {
        proxy_pass ${upstream};
        proxy_http_version 1.1;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto https;
    }
}

server {
    listen 80;
    server_name ${domain};
    return 301 https://\$host\$request_uri;
}
NGINX

  ln -sf "/etc/nginx/sites-available/${site_name}.conf" "/etc/nginx/sites-enabled/${site_name}.conf"
  echo "==> Wrote nginx site for https://${domain} -> ${upstream}"
}

configure_nginx() {
  local install_nginx
  install_nginx="$(get_env_value INSTALL_NGINX || true)"

  if [[ "$install_nginx" != "true" ]]; then
    if [[ -t 0 ]]; then
      ask_yes_no "Configure nginx reverse proxy and SSL now?" "y" || return 0
    else
      return 0
    fi
  fi

  say "Configuring nginx reverse proxy"

  local current_public bot_default bot_domain root_domain cert_default key_default cert_path key_path app_port
  current_public="$(get_env_value PUBLIC_BASE_URL || true)"
  bot_default="$(url_host "$current_public")"
  if [[ -z "$bot_default" || "$bot_default" == "localhost" || "$bot_default" == "127.0.0.1" ]]; then
    bot_default="bot.example.com"
  fi

  bot_domain="$(ask 'Bot public domain for NOWPayments webhook' "$bot_default")"
  if [[ -z "$bot_domain" || "$bot_domain" == "bot.example.com" ]]; then
    warn "No real bot domain provided; skipping nginx config. Set PUBLIC_BASE_URL later."
    return 0
  fi

  root_domain="$(root_domain_from_host "$bot_domain")"
  cert_default="$(get_env_value NGINX_SSL_CERT || true)"
  key_default="$(get_env_value NGINX_SSL_KEY || true)"
  [[ -z "$cert_default" ]] && cert_default="/root/cert/${root_domain}/fullchain.pem"
  [[ -z "$key_default" ]] && key_default="/root/cert/${root_domain}/privkey.pem"

  cert_path="$(ask 'SSL fullchain path' "$cert_default")"
  key_path="$(ask 'SSL key path' "$key_default")"

  if [[ ! -f "$cert_path" || ! -f "$key_path" ]]; then
    warn "SSL cert/key not found. Expected: $cert_path and $key_path"
    warn "Skipping nginx config. Put your TLS certificate files there and re-run sudo bash install.sh."
    return 0
  fi

  app_port="$(get_env_value APP_PORT || true)"
  [[ -z "$app_port" ]] && app_port="8080"

  set_env_value PUBLIC_BASE_URL "https://${bot_domain}"
  set_env_value APP_HOST "127.0.0.1"
  set_env_value APP_PORT "$app_port"
  set_env_value NGINX_SSL_CERT "$cert_path"
  set_env_value NGINX_SSL_KEY "$key_path"

  write_nginx_proxy_site "$bot_domain" "http://127.0.0.1:${app_port}" "$cert_path" "$key_path" "bot"

  local sub_proxy sub_base xui_base sub_domain_default sub_domain upstream_default upstream path_template
  sub_proxy="$(get_env_value NGINX_SUBSCRIPTION_PROXY || true)"
  sub_base="$(get_env_value XUI_SUBSCRIPTION_BASE_URL || true)"
  xui_base="$(get_env_value XUI_BASE_URL || true)"
  sub_domain_default="$(url_host "$sub_base")"
  if [[ -z "$sub_domain_default" ]]; then
    sub_domain_default="$(url_host "$xui_base")"
  fi

  if [[ "$sub_proxy" == "true" ]] || { [[ -t 0 ]] && ask_yes_no "Configure a second nginx proxy for subscription links without port?" "n"; }; then
    sub_domain="$(ask 'Public subscription domain, for example viento.example.com' "$sub_domain_default")"
    upstream_default="$(get_env_value NGINX_SUBSCRIPTION_PROXY_TARGET || true)"
    if [[ -z "$upstream_default" ]]; then
      upstream_default="$xui_base"
    fi
    upstream="$(ask 'Subscription upstream target with real port, for example https://127.0.0.1:25567' "$upstream_default")"
    path_template="$(ask 'Subscription path template' "$(get_env_value XUI_SUBSCRIPTION_PATH_TEMPLATE || true)")"
    [[ -z "$path_template" ]] && path_template="/sub/{sub_id}"

    if [[ -n "$sub_domain" && -n "$upstream" ]]; then
      set_env_value XUI_SUBSCRIPTION_BASE_URL "https://${sub_domain}"
      set_env_value XUI_SUBSCRIPTION_PATH_TEMPLATE "$path_template"
      set_env_value NGINX_SUBSCRIPTION_PROXY "true"
      set_env_value NGINX_SUBSCRIPTION_PROXY_TARGET "$upstream"
      write_nginx_proxy_site "$sub_domain" "$upstream" "$cert_path" "$key_path" "sub"
    else
      warn "Subscription proxy skipped because domain or upstream was empty."
    fi
  fi

  nginx -t
  systemctl enable --now nginx
  systemctl reload nginx
}

echo "==> Installing system packages"
apt-get update
apt-get install -y python3 python3-venv python3-pip git curl unzip nginx

cd "$APP_DIR"
configure_env_interactive
configure_nginx

if ! grep -q '^BOT_TOKEN=' .env || grep -q '^BOT_TOKEN=PUT_TELEGRAM_BOT_TOKEN_HERE' .env; then
  echo
  echo "BOT_TOKEN is not set yet. Edit it now:"
  echo "  nano $APP_DIR/.env"
  echo
fi

echo "==> Creating virtualenv"
$PYTHON_BIN -m venv .venv

# shellcheck disable=SC1091
source .venv/bin/activate
python -m pip install --upgrade pip wheel setuptools
pip install -r requirements.txt

echo "==> Writing systemd service"
cat > "$SERVICE_FILE" <<SERVICE
[Unit]
Description=Telegram 3x-ui Shop Bot
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=${APP_DIR}
Environment=PYTHONUNBUFFERED=1
ExecStart=${APP_DIR}/.venv/bin/python ${APP_DIR}/run.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
SERVICE

systemctl daemon-reload
systemctl enable --now "$APP_NAME"

echo
echo "Install complete. The bot service is enabled and started."
echo "Edit config if needed: nano $APP_DIR/.env"
echo "Restart after config changes: systemctl restart $APP_NAME"
echo "Status: systemctl status $APP_NAME --no-pager"
echo "Logs: journalctl -u $APP_NAME -f"
echo "Health check: curl $(get_env_value PUBLIC_BASE_URL || true)/health"
