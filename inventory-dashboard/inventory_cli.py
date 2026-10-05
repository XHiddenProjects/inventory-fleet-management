#!/usr/bin/env python3
"""
inventory_cli.py - talks to the inventory server's admin API on behalf of
the Cockpit page.

Run as root via cockpit.spawn (superuser: "require"), so it can read the
admin token from a root-only config file - the browser/JS side never
needs to know any secret at all, the same pattern used for the Samba AD DC
module's local-root database access.

Usage:
  inventory_cli.py dashboard
  inventory_cli.py agents
  inventory_cli.py agent <agent_id>
  inventory_cli.py agent-delete <agent_id>
  inventory_cli.py agent-revoke <agent_id>
  inventory_cli.py agent-tags <agent_id> <json-array-of-tags>
  inventory_cli.py jobs
  inventory_cli.py job <job_id>
  inventory_cli.py create-job   (reads a JSON body from stdin)
  inventory_cli.py enrollment-keys
  inventory_cli.py create-enrollment-key   (reads a JSON body from stdin)
  inventory_cli.py delete-enrollment-key <key_hash>
  inventory_cli.py files
  inventory_cli.py delete-file <file_id>
  inventory_cli.py upload-file <filename> <platform> <label> <silent_args>
                                (reads raw file bytes from stdin)

Always prints one JSON object to stdout: {"ok": true, "data": ...} or
{"ok": false, "error": "..."} - so the Cockpit-side JS never has to parse
partial/garbled output.
"""
import json
import sys
from pathlib import Path
from urllib import request as urlrequest
from urllib.error import HTTPError, URLError

CONFIG_PATH = Path("/etc/inventory-server/server.env")


def load_config() -> dict:
    cfg = {"INVENTORY_SERVER_URL": "http://127.0.0.1:8787", "INVENTORY_ADMIN_TOKEN": ""}
    if CONFIG_PATH.exists():
        for line in CONFIG_PATH.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            cfg[k.strip()] = v.strip().strip('"').strip("'")
    return cfg


def call(method: str, path: str, body=None):
    cfg = load_config()
    url = cfg["INVENTORY_SERVER_URL"].rstrip("/") + path
    data = json.dumps(body).encode() if body is not None else None
    req = urlrequest.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    req.add_header("Authorization", f"Bearer {cfg['INVENTORY_ADMIN_TOKEN']}")
    with urlrequest.urlopen(req, timeout=15) as resp:
        raw = resp.read()
        return json.loads(raw) if raw else {}


def call_binary(method: str, path: str, data: bytes, headers: dict, timeout: int = 600):
    cfg = load_config()
    url = cfg["INVENTORY_SERVER_URL"].rstrip("/") + path
    req = urlrequest.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/octet-stream")
    req.add_header("Authorization", f"Bearer {cfg['INVENTORY_ADMIN_TOKEN']}")
    for k, v in headers.items():
        req.add_header(k, v)
    with urlrequest.urlopen(req, timeout=timeout) as resp:
        raw = resp.read()
        return json.loads(raw) if raw else {}


def output(ok: bool, data=None, error: str = ""):
    print(json.dumps({"ok": ok, "data": data, "error": error}))


def main():
    if len(sys.argv) < 2:
        output(False, error="No subcommand given")
        sys.exit(1)
    cmd = sys.argv[1]
    args = sys.argv[2:]

    try:
        if cmd == "dashboard":
            output(True, call("GET", "/api/admin/dashboard"))
        elif cmd == "agents":
            output(True, call("GET", "/api/admin/agents"))
        elif cmd == "agent":
            output(True, call("GET", f"/api/admin/agents/{args[0]}"))
        elif cmd == "agent-delete":
            output(True, call("DELETE", f"/api/admin/agents/{args[0]}"))
        elif cmd == "agent-revoke":
            output(True, call("POST", f"/api/admin/agents/{args[0]}/revoke"))
        elif cmd == "agent-tags":
            tags = json.loads(args[1])
            output(True, call("POST", f"/api/admin/agents/{args[0]}/tags", {"tags": tags}))
        elif cmd == "jobs":
            output(True, call("GET", "/api/admin/jobs"))
        elif cmd == "job":
            output(True, call("GET", f"/api/admin/jobs/{args[0]}"))
        elif cmd == "create-job":
            body = json.loads(sys.stdin.read())
            output(True, call("POST", "/api/admin/jobs", body))
        elif cmd == "enrollment-keys":
            output(True, call("GET", "/api/admin/enrollment-keys"))
        elif cmd == "create-enrollment-key":
            body = json.loads(sys.stdin.read())
            output(True, call("POST", "/api/admin/enrollment-keys", body))
        elif cmd == "delete-enrollment-key":
            output(True, call("DELETE", f"/api/admin/enrollment-keys/{args[0]}"))
        elif cmd == "files":
            output(True, call("GET", "/api/admin/files"))
        elif cmd == "delete-file":
            output(True, call("DELETE", f"/api/admin/files/{args[0]}"))
        elif cmd == "upload-file":
            filename, platform_, label, silent_args = args[0], args[1], args[2], args[3]
            raw = sys.stdin.buffer.read()
            headers = {
                "X-Filename": filename,
                "X-Platform": platform_,
                "X-Label": label,
                "X-Silent-Args": silent_args,
            }
            output(True, call_binary("POST", "/api/admin/files", raw, headers))
        elif cmd == "config-status":
            cfg = load_config()
            output(True, {
                "server_url": cfg["INVENTORY_SERVER_URL"],
                "has_token": bool(cfg["INVENTORY_ADMIN_TOKEN"]),
                "config_path_exists": CONFIG_PATH.exists(),
            })
        else:
            output(False, error=f"Unknown subcommand: {cmd}")
            sys.exit(1)
    except HTTPError as e:
        try:
            detail = json.loads(e.read()).get("detail", str(e))
        except Exception:
            detail = str(e)
        output(False, error=f"HTTP {e.code}: {detail}")
        sys.exit(1)
    except URLError as e:
        output(False, error=f"Could not reach inventory server: {e.reason}. Check {CONFIG_PATH}.")
        sys.exit(1)
    except FileNotFoundError:
        output(False, error=f"No config at {CONFIG_PATH}. Run the server installer first.")
        sys.exit(1)
    except Exception as e:
        output(False, error=f"{type(e).__name__}: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
