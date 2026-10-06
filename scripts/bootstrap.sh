#!/bin/bash
# One-line remote installer, the same pattern as a GitHub "curl | bash"
# install - except it must point at somewhere on YOUR network (an internal
# file share, git server, or even the inventory server itself), never the
# public internet. It only downloads the release tarball and runs the
# already-offline install-server.sh from inside it; it does not reach out
# to PyPI, GitHub.com, or anywhere else.
#
# Usage:
#   curl -fsSL http://<your-internal-host>/inventory-fleet-management.tar.gz -o /tmp/ifm.tar.gz
#   curl -fsSL http://<your-internal-host>/bootstrap.sh | sudo bash -s -- --url http://<your-internal-host>/inventory-fleet-management.tar.gz
#
# or simply:
#   sudo ./bootstrap.sh --url http://fileserver.lan/inventory-fleet-management.tar.gz
set -euo pipefail

URL=""
SERVER_IP=""
DEST="/tmp/inventory-fleet-management"

while [ $# -gt 0 ]; do
  case "$1" in
    --url) URL="$2"; shift 2 ;;
    --server-ip) SERVER_IP="$2"; shift 2 ;;
    *) echo "Unknown argument: $1" >&2; exit 1 ;;
  esac
done

if [ -z "$URL" ]; then
  echo "Usage: bootstrap.sh --url <http://internal-host/inventory-fleet-management.tar.gz>" >&2
  echo "The URL must point at a host on YOUR network - this script never contacts the public internet." >&2
  exit 1
fi

if [ "$(id -u)" -ne 0 ]; then
  echo "Run this as root (sudo)." >&2
  exit 1
fi

echo "Fetching $URL ..."
rm -rf "$DEST" "$DEST.tar.gz"
curl -fsSL "$URL" -o "$DEST.tar.gz"

mkdir -p "$DEST"
tar -xzf "$DEST.tar.gz" -C "$DEST" --strip-components=1

echo "Running install-server.sh ..."
chmod +x "$DEST/scripts/install-server.sh"
if [ -n "$SERVER_IP" ]; then
  "$DEST/scripts/install-server.sh" --server-ip "$SERVER_IP"
else
  "$DEST/scripts/install-server.sh"
fi
