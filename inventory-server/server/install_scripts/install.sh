#!/bin/bash
# Inventory agent installer for Linux (systemd).
# Usage: curl -fsSL <server>/agent/install.sh | sudo bash -s -- --server <url> --enrollment-key <key>
set -euo pipefail

SERVER=""
ENROLLMENT_KEY=""
INTERVAL=60
INSTALL_DIR="/opt/inventory-agent"

while [ $# -gt 0 ]; do
  case "$1" in
    --server) SERVER="$2"; shift 2 ;;
    --enrollment-key) ENROLLMENT_KEY="$2"; shift 2 ;;
    --interval) INTERVAL="$2"; shift 2 ;;
    --install-dir) INSTALL_DIR="$2"; shift 2 ;;
    *) echo "Unknown argument: $1" >&2; exit 1 ;;
  esac
done

if [ -z "$SERVER" ] || [ -z "$ENROLLMENT_KEY" ]; then
  echo "Usage: install.sh --server <url> --enrollment-key <key> [--interval <seconds>] [--install-dir <path>]" >&2
  exit 1
fi

if [ "$(id -u)" -ne 0 ]; then
  echo "This installer must be run as root (sudo)." >&2
  exit 1
fi

if ! command -v python3 >/dev/null 2>&1; then
  echo "python3 is required but was not found. Install Python 3 (e.g. 'apt install python3 python3-venv') and re-run." >&2
  exit 1
fi

echo "Installing inventory agent to $INSTALL_DIR ..."
mkdir -p "$INSTALL_DIR/collectors"

for f in \
  inventory_agent.py \
  collectors/__init__.py \
  collectors/system.py \
  collectors/software.py \
  collectors/processes.py \
  collectors/network.py \
  collectors/identity.py \
  collectors/services.py
do
  curl -fsSL "$SERVER/agent/files/$f" -o "$INSTALL_DIR/$f"
done

# psutil comes from the inventory server itself (vendor/wheels), never PyPI,
# so this install needs LAN access to $SERVER only - no internet required.
ARCH="$(uname -m)"
case "$ARCH" in
  x86_64|amd64) WHEEL="psutil-7.2.2-cp36-abi3-manylinux2010_x86_64.manylinux_2_12_x86_64.manylinux_2_28_x86_64.whl" ;;
  aarch64|arm64) WHEEL="psutil-7.2.2-cp36-abi3-manylinux2014_aarch64.manylinux_2_17_aarch64.manylinux_2_28_aarch64.whl" ;;
  *) echo "Unsupported architecture: $ARCH" >&2; exit 1 ;;
esac
mkdir -p "$INSTALL_DIR/vendor"
curl -fsSL "$SERVER/agent/files/vendor/wheels/$WHEEL" -o "$INSTALL_DIR/vendor/$WHEEL"

if python3 -m venv "$INSTALL_DIR/venv" 2>/dev/null && [ -x "$INSTALL_DIR/venv/bin/pip" ]; then
  "$INSTALL_DIR/venv/bin/pip" install --quiet --no-index "$INSTALL_DIR/vendor/$WHEEL"
  PYBIN="$INSTALL_DIR/venv/bin/python3"
else
  echo "Warning: could not create a venv (missing python3-venv?); falling back to the system Python." >&2
  python3 -m pip install --quiet --user --no-index "$INSTALL_DIR/vendor/$WHEEL" || pip3 install --quiet --user --no-index "$INSTALL_DIR/vendor/$WHEEL"
  PYBIN="$(command -v python3)"
fi

cat > /etc/systemd/system/inventory-agent.service <<UNIT
[Unit]
Description=Inventory Agent
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
ExecStart=$PYBIN $INSTALL_DIR/inventory_agent.py --server $SERVER --enrollment-key $ENROLLMENT_KEY --interval $INTERVAL
Restart=always
RestartSec=15
User=root

[Install]
WantedBy=multi-user.target
UNIT

systemctl daemon-reload
systemctl enable --now inventory-agent.service

echo "Done. Check status with: systemctl status inventory-agent"
echo "Logs with: journalctl -u inventory-agent -f"
