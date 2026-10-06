# Inventory & Fleet Management

[MIT licensed](LICENSE)

A self-hosted inventory/fleet agent system: lightweight agents report system,
software, process, network, identity, and service info to a central server;
a Cockpit dashboard page gives you a web UI for enrolling, browsing, and
managing them.

**Nothing in this project talks to the public internet at runtime.**
Agents only ever call the `--server` URL you give them (your LAN server),
the server has no outbound calls at all, and the Cockpit dashboard is
static files + one local CLI helper. The only place internet was ever
needed is *installing* third-party Python packages (FastAPI, psutil,
etc.) — and this release has those **vendored** as wheel files, so even
install needs no internet. See [Offline guarantee](#offline-guarantee)
below for exactly what that covers.

## Layout

```
inventory-server/
  server/            FastAPI server (API, DB, install scripts, vendored deps)
  agent/              Agent source + collectors, vendored psutil wheels
inventory-dashboard/  Cockpit page (static JS/HTML/CSS + root-run CLI helper)
agent-windows-exe-kit/ PyInstaller kit to build a standalone agent .exe
scripts/
  install-server.sh    Install the server + Cockpit dashboard (run locally)
  uninstall-server.sh  Remove them
  bootstrap.sh          curl-pipeable installer that fetches a release
                         tarball from YOUR OWN server and runs install-server.sh
```

## Installing the server (two ways, like a GitHub project)

### Method 1 — download & run locally (recommended)

```bash
tar -xzf inventory-fleet-management.tar.gz
cd inventory-fleet-management
sudo ./scripts/install-server.sh
```

If the server has multiple network interfaces, specify the address that agents should use:

```bash
sudo ./scripts/install-server.sh --server-ip 10.200.0.10
```

This installs to `/opt/inventory-server`, sets up a `inventory-server`
systemd service listening on `0.0.0.0:8787`, generates an admin token in
`/etc/inventory-server/server.env`, and — if Cockpit is present — deploys
the dashboard to `/usr/share/cockpit/inventory-dashboard` (look for
"Inventory" in the Cockpit menu afterwards).

### Method 2 — one-line remote install

Host the release tarball and `scripts/bootstrap.sh` somewhere on your own
network (an internal file share, git server, or even a plain `python3 -m
http.server` on a workstation), then on the target box:

```bash
curl -fsSL http://your-internal-host/bootstrap.sh \
  | sudo bash -s -- --url http://your-internal-host/inventory-fleet-management.tar.gz --server-ip 10.200.0.10
```

This is the same "curl | bash" pattern you'd see for a GitHub-hosted
project, except the URL must point somewhere on your own network —
`bootstrap.sh` never contacts the public internet, it just fetches the
tarball you host and hands off to `install-server.sh`.

Uninstall either way with `sudo ./scripts/uninstall-server.sh` (add
`--purge` to also delete the database and admin token).

## Getting this onto GitHub

This folder is already a git repo (`git log` to confirm). To push it:

```bash
# on github.com, create a new empty repo first (no README/license/.gitignore,
# to avoid merge conflicts with what's already committed here), then:
cd inventory-fleet-management
git remote add origin git@github.com:<you>/inventory-fleet-management.git
git branch -M main
git push -u origin main
```

From there, "Method 2" above still works the same way, just point `--url`
at a GitHub release asset instead of an internal file server — e.g. after
cutting a GitHub Release and attaching this tarball:

```bash
curl -fsSL https://raw.githubusercontent.com/<you>/inventory-fleet-management/main/scripts/bootstrap.sh \
  | sudo bash -s -- --url https://github.com/<you>/inventory-fleet-management/releases/download/v1.0.0/inventory-fleet-management.tar.gz
```

Note this is the one case in this project that *does* touch the public
internet — fetching from github.com itself. Everything downstream of that
(the actual install, and all agent↔server traffic afterwards) stays
exactly as offline as described below.

## Installing agents

Once the server is running, its dashboard's **Enrollment** page gives you
ready-to-paste commands per OS, all served by the server itself (LAN
only):

- **Linux**: `curl -fsSL $SERVER/agent/install.sh | sudo bash -s -- --server $SERVER --enrollment-key ...`
- **macOS**: `curl -fsSL $SERVER/agent/install-macos.sh | sudo bash -s -- --server $SERVER --enrollment-key ...`
- **Windows**: `iwr -useb $SERVER/agent/install.ps1 | iex; Install-InventoryAgent -Server $SERVER -EnrollmentKey ...`

All three download `psutil` from the server's vendored wheels
(`/agent/files/vendor/wheels/...`), not PyPI — so agent installs also need
only LAN access to your server, never the internet.

### Windows hosts with no Python at all (or fully offline)

Use `agent-windows-exe-kit/` instead: build `inventory-agent.exe` once on
any Windows machine with Python (`build.ps1`), then copy that single exe
to as many offline hosts as you like and install with `install-exe.ps1`.
No Python, pip, or network access beyond reaching your server is needed
on the target machine at all. See the comments in that folder's scripts
for details.

## Offline guarantee

| Step | Needs internet? |
|---|---|
| Agent ↔ server traffic (enroll, check-in, jobs) | **No** — only reaches the `--server` URL you configure |
| Server → anywhere | **No** — no outbound calls in `server/*.py` |
| Cockpit dashboard → anywhere | **No** — static files + local `inventory_cli.py`, no CDN assets |
| Installing the server (`install-server.sh`) | **No** — deps installed from `server/vendor/wheels` via `pip --no-index` |
| Installing an agent via script (`install.sh`/`install-macos.sh`/`install.ps1`) | **No** — `psutil` fetched from the server's `/agent/files/vendor/wheels`, not PyPI |
| Installing an agent via the `.exe` | **No** — fully self-contained, nothing to fetch |
| `scripts/bootstrap.sh` | **No**, as long as `--url` points at a host on your own network |
| **Building** `inventory-agent.exe` in the first place (`agent-windows-exe-kit/build.ps1`) | **Yes, once** — PyInstaller + psutil are pulled from PyPI on the build machine only, never on the install targets |

Everything except `pydantic_core` is a pure-Python (`py3-none-any`) wheel,
so it's version/platform-agnostic. `pydantic_core` is compiled and is the
one package that has to match the target's exact Python minor version +
OS/arch:

- Server (`inventory-server/server/vendor/wheels`): `pydantic_core` for
  Python 3.9–3.13 on Linux x86_64, and Python 3.12 on Linux aarch64.
- Agent (`inventory-server/agent/vendor/wheels`): `psutil` ships `abi3`
  wheels, so one file per platform covers Python 3.6+ — Linux x86_64/aarch64,
  macOS x86_64/arm64, and Windows x86_64 are all included.

If a target server runs a Python/arch combo not listed above (e.g. 3.14,
or aarch64 on a different Python version), `pip --no-index` will fail with
"could not find a version that satisfies ... pydantic-core". Fix it by
running this on **any machine matching that target's OS/arch/Python
version** (it doesn't need to be the offline target itself, and needs
internet only for this one step):

```bash
pip download --no-deps --only-binary=:all: pydantic_core==<version from requirements/installed> \
  -d inventory-server/server/vendor/wheels
```

or cross-download without needing a matching machine at all, from anywhere pip can reach PyPI:

```bash
pip download --no-deps --only-binary=:all: \
  --platform manylinux2014_x86_64 --python-version 3.14 \
  pydantic_core==<version> -d inventory-server/server/vendor/wheels
```

Then drop the resulting `.whl` into that folder and re-run the install —
no other vendored packages need touching.
