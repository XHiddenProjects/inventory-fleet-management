#!/bin/bash
# Removes the inventory server + Cockpit dashboard. Agent data in
# /var/lib/inventory-server is kept unless --purge is passed.
set -euo pipefail

PURGE=0
[ "${1:-}" = "--purge" ] && PURGE=1

if [ "$(id -u)" -ne 0 ]; then
  echo "Run this as root (sudo)." >&2
  exit 1
fi

systemctl disable --now inventory-server.service 2>/dev/null || true
rm -f /etc/systemd/system/inventory-server.service
systemctl daemon-reload

rm -rf /opt/inventory-server
rm -rf /usr/share/cockpit/inventory-dashboard
systemctl try-restart cockpit.socket 2>/dev/null || true

if [ "$PURGE" = "1" ]; then
  rm -rf /var/lib/inventory-server /etc/inventory-server
  echo "Removed server, dashboard, database, and config."
else
  echo "Removed server and dashboard. Database + admin token kept in /var/lib/inventory-server and /etc/inventory-server (use --purge to delete those too)."
fi
