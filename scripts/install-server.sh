#!/bin/bash
# Installs the Inventory & Fleet Management SERVER + its Cockpit dashboard
# page on this Linux machine. Run locally after extracting the release
# tarball (NOT piped from the internet) - see README.md for the two
# install methods.
#
# Needs no internet access: all Python dependencies are vendored under
# inventory-server/server/vendor/wheels, installed with `pip --no-index`.
#
# Usage (run as root from the extracted release directory):
#   sudo ./scripts/install-server.sh [--port 8787] [--install-dir /opt/inventory-server]
set -euo pipefail

PORT=8787
INSTALL_DIR="/opt/inventory-server"
DB_DIR="/var/lib/inventory-server"
CONFIG_DIR="/etc/inventory-server"
DASHBOARD_DIR="/usr/share/cockpit/inventory-dashboard"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

while [ $# -gt 0 ]; do
  case "$1" in
    --port) PORT="$2"; shift 2 ;;
    --install-dir) INSTALL_DIR="$2"; shift 2 ;;
    *) echo "Unknown argument: $1" >&2; exit 1 ;;
  esac
done

if [ "$(id -u)" -ne 0 ]; then
  echo "Run this as root (sudo)." >&2
  exit 1
fi
if ! command -v python3 >/dev/null 2>&1; then
  echo "python3 is required (e.g. 'apt install python3 python3-venv') and was not found." >&2
  exit 1
fi

echo "== Installing server files to $INSTALL_DIR =="
mkdir -p "$INSTALL_DIR"
cp -r "$REPO_ROOT/inventory-server/server" "$INSTALL_DIR/"
cp -r "$REPO_ROOT/inventory-server/agent" "$INSTALL_DIR/"

echo "== Setting up Python venv (offline, from vendored wheels) =="
python3 -m venv "$INSTALL_DIR/venv"
"$INSTALL_DIR/venv/bin/pip" install --quiet --no-index \
  --find-links="$INSTALL_DIR/server/vendor/wheels" \
  -r "$INSTALL_DIR/server/requirements.txt"

echo "== Creating data + config directories =="
mkdir -p "$DB_DIR" "$CONFIG_DIR"
chmod 700 "$DB_DIR" "$CONFIG_DIR"

if [ ! -f "$CONFIG_DIR/server.env" ]; then
  ADMIN_TOKEN="$(python3 -c 'import secrets; print(secrets.token_hex(32))')"
  cat > "$CONFIG_DIR/server.env" <<ENV
INVENTORY_SERVER_URL=http://127.0.0.1:$PORT
INVENTORY_ADMIN_TOKEN=$ADMIN_TOKEN
INVENTORY_DB=$DB_DIR/inventory.db
ENV
  chmod 600 "$CONFIG_DIR/server.env"
  echo "Generated a new admin token in $CONFIG_DIR/server.env"
else
  echo "$CONFIG_DIR/server.env already exists - leaving it as is."
fi
# shellcheck disable=SC1090
source "$CONFIG_DIR/server.env"

echo "== Installing systemd service =="
cat > /etc/systemd/system/inventory-server.service <<UNIT
[Unit]
Description=Inventory & Fleet Management server
After=network.target

[Service]
Type=simple
EnvironmentFile=$CONFIG_DIR/server.env
ExecStart=$INSTALL_DIR/venv/bin/python3 -m uvicorn server.api:app --app-dir $INSTALL_DIR --host 0.0.0.0 --port $PORT
Restart=always
RestartSec=5
User=root

[Install]
WantedBy=multi-user.target
UNIT

systemctl daemon-reload
systemctl enable --now inventory-server.service

echo "== Installing Cockpit dashboard page =="
if command -v cockpit-bridge >/dev/null 2>&1 || [ -d /usr/share/cockpit ]; then
  mkdir -p "$DASHBOARD_DIR"
  cp -r "$REPO_ROOT/inventory-dashboard/"* "$DASHBOARD_DIR/"
  systemctl try-restart cockpit.socket 2>/dev/null || true
  echo "Dashboard installed to $DASHBOARD_DIR - open Cockpit and look for 'Inventory' in the menu."
else
  echo "Cockpit does not appear to be installed - skipping dashboard deployment."
  echo "Install Cockpit first (e.g. 'apt install cockpit' / 'dnf install cockpit'), then re-run this script."
fi

echo ""
echo "== Done =="
echo "Server:      listening on 0.0.0.0:$PORT  (systemctl status inventory-server)"
echo "Admin token: $CONFIG_DIR/server.env (INVENTORY_ADMIN_TOKEN)"
echo ""
echo "Point agents at this box's LAN IP/hostname, e.g.:"
echo "  http://$(hostname -I 2>/dev/null | awk '{print $1}'):$PORT"
echo "No internet is used anywhere in this install or at runtime - only LAN access between agents, the server, and this Cockpit host."
