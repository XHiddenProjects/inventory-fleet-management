"""Services collector. Genuinely three different service managers."""
import platform
import shutil
import subprocess


def _run(cmd, timeout=20):
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return out.stdout
    except Exception:
        return ""


def _linux():
    if not shutil.which("systemctl"):
        return []
    out = _run([
        "systemctl", "list-units", "--type=service", "--all", "--no-legend", "--no-pager", "--plain"
    ])
    services = []
    for line in out.splitlines():
        parts = line.split(None, 4)
        if len(parts) < 4:
            continue
        unit, load, active, sub = parts[0], parts[1], parts[2], parts[3]
        description = parts[4] if len(parts) > 4 else ""
        # enabled/disabled/static/masked - separate call, only cheap because
        # we only do it for services that actually loaded.
        start_mode = ""
        if load == "loaded":
            enabled_out = _run(["systemctl", "is-enabled", unit], timeout=3)
            start_mode = enabled_out.strip()
        services.append({
            "name": unit.removesuffix(".service"),
            "display_name": description,
            "status": active,       # active | inactive | failed
            "sub_status": sub,      # running | dead | exited | ...
            "start_mode": start_mode,  # enabled | disabled | static | masked
        })
    return services


def _windows():
    ps = (
        "Get-Service | Select-Object Name, DisplayName, Status, StartType | "
        "ForEach-Object { \"$($_.Name)`t$($_.DisplayName)`t$($_.Status)`t$($_.StartType)\" }"
    )
    out = _run(["powershell", "-NoProfile", "-Command", ps], timeout=30)
    services = []
    for line in out.splitlines():
        parts = line.split("\t")
        if len(parts) < 4:
            continue
        name, display_name, status, start_type = parts
        services.append({
            "name": name,
            "display_name": display_name,
            "status": status.strip().lower(),      # running | stopped | ...
            "sub_status": "",
            "start_mode": start_type.strip().lower(),  # automatic | manual | disabled
        })
    return services


def _macos():
    if not shutil.which("launchctl"):
        return []
    out = _run(["launchctl", "list"])
    services = []
    for line in out.splitlines()[1:]:
        parts = line.split("\t")
        if len(parts) < 3:
            continue
        pid, status, label = parts[0], parts[1], parts[2]
        services.append({
            "name": label,
            "display_name": label,
            "status": "running" if pid != "-" else "stopped",
            "sub_status": f"last_exit_status={status}",
            "start_mode": "",
        })
    return services


def collect() -> dict:
    system = platform.system()
    if system == "Linux":
        services = _linux()
    elif system == "Windows":
        services = _windows()
    elif system == "Darwin":
        services = _macos()
    else:
        services = []
    running = sum(1 for s in services if s["status"] in ("running", "active"))
    return {"services": services, "count": len(services), "running_count": running}


if __name__ == "__main__":
    import json
    d = collect()
    print(json.dumps({"count": d["count"], "running_count": d["running_count"], "sample": d["services"][:8]}, indent=2, default=str))
