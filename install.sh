#!/usr/bin/env bash
set -euo pipefail

APP_NAME="telegram-3xui-shop-bot"
APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON_BIN="${PYTHON_BIN:-python3}"
SERVICE_FILE="/etc/systemd/system/${APP_NAME}.service"

if [[ "${EUID}" -ne 0 ]]; then
  echo "Please run as root: sudo bash install.sh"
  exit 1
fi

echo "==> Installing system packages"
apt-get update
apt-get install -y python3 python3-venv python3-pip git curl unzip

cd "$APP_DIR"

if [[ ! -f .env ]]; then
  cp .env.example .env
  echo "==> Created .env from .env.example"
fi

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
systemctl enable "$APP_NAME"

echo
echo "Install complete."
echo "1) Edit token if needed: nano $APP_DIR/.env"
echo "2) Start bot: systemctl start $APP_NAME"
echo "3) Logs: journalctl -u $APP_NAME -f"
