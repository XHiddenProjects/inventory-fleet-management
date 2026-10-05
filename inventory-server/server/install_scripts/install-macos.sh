#!/bin/bash
# Inventory agent installer for macOS (launchd).
# Usage: curl -fsSL <server>/agent/install-macos.sh | sudo bash -s -- --server <url> --enrollment-key <key>
set -euo pipefail

SERVER=""
ENROLLMENT_KEY=""
INTERVAL=60
INSTALL_DIR="/usr/local/inventory-agent"

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
  echo "Usage: install-macos.sh --server <url> --enrollment-key <key> [--interval <seconds>] [--install-dir <path>]" >&2
  exit 1
fi

if [ "$(id -u)" -ne 0 ]; then
  echo "This installer must be run as root (sudo)." >&2
  exit 1
fi

if ! command -v python3 >/dev/null 2>&1; then
  echo "python3 is required but was not found. Install it from https://www.python.org/downloads/macos/ or 'brew install python3', then re-run." >&2
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
  arm64) WHEEL="psutil-7.2.2-cp36-abi3-macosx_11_0_arm64.whl" ;;
  x86_64) WHEEL="psutil-7.2.2-cp36-abi3-macosx_10_9_x86_64.whl" ;;
  *) echo "Unsupported architecture: $ARCH" >&2; exit 1 ;;
esac
mkdir -p "$INSTALL_DIR/vendor"
curl -fsSL "$SERVER/agent/files/vendor/wheels/$WHEEL" -o "$INSTALL_DIR/vendor/$WHEEL"

if python3 -m venv "$INSTALL_DIR/venv" 2>/dev/null && [ -x "$INSTALL_DIR/venv/bin/pip" ]; then
  "$INSTALL_DIR/venv/bin/pip" install --quiet --no-index "$INSTALL_DIR/vendor/$WHEEL"
  PYBIN="$INSTALL_DIR/venv/bin/python3"
else
  echo "Warning: could not create a venv; falling back to the system Python." >&2
  python3 -m pip install --quiet --user --no-index "$INSTALL_DIR/vendor/$WHEEL" || pip3 install --quiet --user --no-index "$INSTALL_DIR/vendor/$WHEEL"
  PYBIN="$(command -v python3)"
fi

PLIST="/Library/LaunchDaemons/com.inventory.agent.plist"
cat > "$PLIST" <<PLIST_EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>com.inventory.agent</string>
  <key>ProgramArguments</key>
  <array>
    <string>$PYBIN</string>
    <string>$INSTALL_DIR/inventory_agent.py</string>
    <string>--server</string><string>$SERVER</string>
    <string>--enrollment-key</string><string>$ENROLLMENT_KEY</string>
    <string>--interval</string><string>$INTERVAL</string>
  </array>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>StandardOutPath</key><string>/var/log/inventory-agent.log</string>
  <key>StandardErrorPath</key><string>/var/log/inventory-agent.err.log</string>
</dict>
</plist>
PLIST_EOF

chown root:wheel "$PLIST"
chmod 644 "$PLIST"

launchctl unload "$PLIST" 2>/dev/null || true
launchctl load -w "$PLIST"

echo "Done. Logs: /var/log/inventory-agent.log"
echo "Check status with: launchctl list | grep com.inventory.agent"
