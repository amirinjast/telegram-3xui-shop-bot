#!/usr/bin/env bash
set -euo pipefail

APP_NAME="telegram-3xui-shop-bot"
APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$APP_DIR"

echo "==> Updating source"
if [[ -d .git ]]; then
  git pull --ff-only
else
  echo "No .git directory found; skipping git pull."
fi

if [[ ! -d .venv ]]; then
  python3 -m venv .venv
fi

# shellcheck disable=SC1091
source .venv/bin/activate
python -m pip install --upgrade pip wheel setuptools
pip install -r requirements.txt

if command -v systemctl >/dev/null 2>&1 && systemctl list-unit-files | grep -q "^${APP_NAME}.service"; then
  echo "==> Restarting systemd service"
  systemctl restart "$APP_NAME"
  systemctl --no-pager --full status "$APP_NAME" || true
else
  echo "Systemd service not found. Run manually with:"
  echo "  source .venv/bin/activate && python run.py"
fi
