"""System & hardware collector. Dispatches per OS; psutil covers most of
the cross-platform surface (CPU, memory, disks, boot time)."""
import platform
import socket
import subprocess
import time

import psutil


def _run(cmd, timeout=5):
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return out.stdout.strip()
    except Exception:
        return ""


def _disks():
    disks = []
    for part in psutil.disk_partitions(all=False):
        try:
            usage = psutil.disk_usage(part.mountpoint)
        except (PermissionError, OSError):
            continue
        disks.append({
            "device": part.device,
            "mountpoint": part.mountpoint,
            "fstype": part.fstype,
            "total_bytes": usage.total,
            "used_bytes": usage.used,
            "free_bytes": usage.free,
            "percent_used": usage.percent,
        })
    return disks


def _linux_extra():
    info = {}
    try:
        with open("/etc/os-release") as f:
            kv = dict(line.strip().split("=", 1) for line in f if "=" in line)
        info["distro"] = kv.get("PRETTY_NAME", "").strip('"')
    except Exception:
        info["distro"] = ""
    info["kernel"] = platform.release()
    # CPU model
    try:
        with open("/proc/cpuinfo") as f:
            for line in f:
                if line.lower().startswith("model name"):
                    info["cpu_model"] = line.split(":", 1)[1].strip()
                    break
    except Exception:
        pass
    return info


def _windows_extra():
    info = {}
    info["cpu_model"] = platform.processor()
    # Best-effort BIOS/product info via wmic (deprecated but still present on
    # most Windows 10/11 installs; PowerShell CIM is the future-proof path).
    manuf = _run(["powershell", "-NoProfile", "-Command",
                  "(Get-CimInstance Win32_ComputerSystem).Manufacturer"])
    model = _run(["powershell", "-NoProfile", "-Command",
                  "(Get-CimInstance Win32_ComputerSystem).Model"])
    info["manufacturer"] = manuf
    info["model"] = model
    return info


def _macos_extra():
    info = {}
    info["cpu_model"] = _run(["sysctl", "-n", "machdep.cpu.brand_string"])
    info["macos_product_version"] = _run(["sw_vers", "-productVersion"])
    info["macos_build"] = _run(["sw_vers", "-buildVersion"])
    return info


def collect() -> dict:
    system = platform.system()  # "Linux" | "Windows" | "Darwin"
    os_key = {"Linux": "linux", "Windows": "windows", "Darwin": "macos"}.get(system, system.lower())

    vm = psutil.virtual_memory()
    swap = psutil.swap_memory()
    try:
        load1, load5, load15 = (psutil.getloadavg() if hasattr(psutil, "getloadavg") else (None, None, None))
    except Exception:
        load1 = load5 = load15 = None

    data = {
        "hostname": socket.gethostname(),
        "fqdn": socket.getfqdn(),
        "os": os_key,
        "os_release": platform.release(),
        "os_version_full": platform.version(),
        "arch": platform.machine(),
        "python_version": platform.python_version(),
        "boot_time": psutil.boot_time(),
        "uptime_seconds": time.time() - psutil.boot_time(),
        "cpu_logical_cores": psutil.cpu_count(logical=True),
        "cpu_physical_cores": psutil.cpu_count(logical=False),
        "cpu_percent": psutil.cpu_percent(interval=0.3),
        "load_avg_1_5_15": [load1, load5, load15],
        "memory_total_bytes": vm.total,
        "memory_used_bytes": vm.used,
        "memory_percent": vm.percent,
        "swap_total_bytes": swap.total,
        "swap_used_bytes": swap.used,
        "disks": _disks(),
    }

    if os_key == "linux":
        data.update(_linux_extra())
    elif os_key == "windows":
        data.update(_windows_extra())
    elif os_key == "macos":
        data.update(_macos_extra())

    return data


if __name__ == "__main__":
    import json
    print(json.dumps(collect(), indent=2, default=str))
