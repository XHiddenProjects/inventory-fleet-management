"""Installed software collector. There is no single API for this on any
OS, so each platform tries its native package managers in turn and merges
whatever's actually present."""
import platform
import shutil
import subprocess


def _run(cmd, timeout=30):
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return out.stdout
    except Exception:
        return ""


def _have(cmd):
    return shutil.which(cmd) is not None


def _linux_dpkg():
    if not _have("dpkg-query"):
        return []
    out = _run(["dpkg-query", "-W", "-f=${Package}\t${Version}\t${Maintainer}\t${Installed-Size}\n"])
    pkgs = []
    for line in out.splitlines():
        parts = line.split("\t")
        if len(parts) < 4:
            continue
        name, version, vendor, size_kb = parts
        try:
            size_bytes = int(size_kb) * 1024
        except ValueError:
            size_bytes = None
        pkgs.append({"name": name, "version": version, "vendor": vendor, "size_bytes": size_bytes, "source": "dpkg"})
    return pkgs


def _linux_rpm():
    if not _have("rpm"):
        return []
    out = _run(["rpm", "-qa", "--qf", "%{NAME}\t%{VERSION}-%{RELEASE}\t%{VENDOR}\t%{SIZE}\n"])
    pkgs = []
    for line in out.splitlines():
        parts = line.split("\t")
        if len(parts) < 4:
            continue
        name, version, vendor, size = parts
        pkgs.append({"name": name, "version": version, "vendor": vendor,
                     "size_bytes": int(size) if size.isdigit() else None, "source": "rpm"})
    return pkgs


def _linux_pacman():
    if not _have("pacman"):
        return []
    out = _run(["pacman", "-Q"])
    pkgs = []
    for line in out.splitlines():
        parts = line.split(" ", 1)
        if len(parts) == 2:
            pkgs.append({"name": parts[0], "version": parts[1], "vendor": "", "size_bytes": None, "source": "pacman"})
    return pkgs


def _linux_snap():
    if not _have("snap"):
        return []
    out = _run(["snap", "list"])
    pkgs = []
    for line in out.splitlines()[1:]:
        parts = line.split()
        if len(parts) >= 2:
            pkgs.append({"name": parts[0], "version": parts[1], "vendor": "", "size_bytes": None, "source": "snap"})
    return pkgs


def _linux_flatpak():
    if not _have("flatpak"):
        return []
    out = _run(["flatpak", "list", "--columns=application,version"])
    pkgs = []
    for line in out.splitlines():
        parts = line.split("\t")
        if len(parts) >= 1 and parts[0]:
            pkgs.append({"name": parts[0], "version": parts[1] if len(parts) > 1 else "", "vendor": "", "size_bytes": None, "source": "flatpak"})
    return pkgs


def _linux():
    pkgs = _linux_dpkg() or _linux_rpm() or _linux_pacman()
    pkgs += _linux_snap()
    pkgs += _linux_flatpak()
    return pkgs


def _windows():
    """Installed programs via the registry Uninstall keys (what Add/Remove
    Programs itself reads) using PowerShell, covering both native and WOW64
    (32-bit-on-64-bit) install locations plus per-user installs."""
    ps = (
        "$paths = @("
        "'HKLM:\\Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\*',"
        "'HKLM:\\Software\\WOW6432Node\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\*',"
        "'HKCU:\\Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\*');"
        "Get-ItemProperty $paths -ErrorAction SilentlyContinue | "
        "Where-Object { $_.DisplayName } | "
        "Select-Object DisplayName, DisplayVersion, Publisher, EstimatedSize | "
        "ForEach-Object { \"$($_.DisplayName)`t$($_.DisplayVersion)`t$($_.Publisher)`t$($_.EstimatedSize)\" }"
    )
    out = _run(["powershell", "-NoProfile", "-Command", ps], timeout=60)
    pkgs = []
    seen = set()
    for line in out.splitlines():
        parts = line.split("\t")
        if not parts or not parts[0].strip():
            continue
        name = parts[0].strip()
        if name in seen:
            continue
        seen.add(name)
        version = parts[1].strip() if len(parts) > 1 else ""
        vendor = parts[2].strip() if len(parts) > 2 else ""
        size_kb = parts[3].strip() if len(parts) > 3 else ""
        pkgs.append({
            "name": name, "version": version, "vendor": vendor,
            "size_bytes": int(size_kb) * 1024 if size_kb.isdigit() else None,
            "source": "registry",
        })
    return pkgs


def _macos():
    pkgs = []
    # System + Mac App Store installed applications
    out = _run(["system_profiler", "SPApplicationsDataType", "-json"], timeout=60)
    if out:
        import json
        try:
            apps = json.loads(out).get("SPApplicationsDataType", [])
            for a in apps:
                pkgs.append({
                    "name": a.get("_name", ""),
                    "version": a.get("version", ""),
                    "vendor": a.get("obtained_from", ""),
                    "size_bytes": None,
                    "source": "applications",
                })
        except Exception:
            pass
    # Homebrew, if present
    if _have("brew"):
        out = _run(["brew", "list", "--versions"])
        for line in out.splitlines():
            parts = line.split()
            if len(parts) >= 2:
                pkgs.append({"name": parts[0], "version": parts[-1], "vendor": "", "size_bytes": None, "source": "homebrew"})
    return pkgs


def collect() -> dict:
    system = platform.system()
    if system == "Linux":
        pkgs = _linux()
    elif system == "Windows":
        pkgs = _windows()
    elif system == "Darwin":
        pkgs = _macos()
    else:
        pkgs = []
    return {"packages": pkgs, "count": len(pkgs)}


if __name__ == "__main__":
    import json
    d = collect()
    print(json.dumps({"count": d["count"], "sample": d["packages"][:5]}, indent=2, default=str))
