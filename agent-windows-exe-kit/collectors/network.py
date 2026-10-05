"""Network collector: interfaces/addresses via psutil (cross-platform),
listening ports and active connections via psutil, plus a best-effort
default gateway/DNS lookup that does need small per-OS commands."""
import platform
import socket
import subprocess

import psutil

# psutil exposes AF_LINK (MAC addresses) as its own pseudo-constant since
# the stdlib socket module doesn't have a portable equivalent.
_AF_LINK = getattr(psutil, "AF_LINK", None)


def _family_name(fam):
    if fam == socket.AF_INET:
        return "ipv4"
    if fam == socket.AF_INET6:
        return "ipv6"
    if _AF_LINK is not None and fam == _AF_LINK:
        return "mac"
    return str(fam)


def _run(cmd, timeout=5):
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return out.stdout.strip()
    except Exception:
        return ""


def _interfaces():
    addrs = psutil.net_if_addrs()
    stats = psutil.net_if_stats()
    out = []
    for name, addr_list in addrs.items():
        st = stats.get(name)
        entry = {
            "name": name,
            "is_up": bool(st.isup) if st else None,
            "speed_mbps": st.speed if st else None,
            "mtu": st.mtu if st else None,
            "addresses": [],
        }
        for a in addr_list:
            entry["addresses"].append({"family": _family_name(a.family), "address": a.address, "netmask": a.netmask})
        out.append(entry)
    return out


def _connections():
    conns = []
    try:
        for c in psutil.net_connections(kind="inet"):
            conns.append({
                "fd": c.fd,
                "family": _family_name(c.family),
                "type": "tcp" if c.type == socket.SOCK_STREAM else "udp",
                "local_address": f"{c.laddr.ip}:{c.laddr.port}" if c.laddr else "",
                "remote_address": f"{c.raddr.ip}:{c.raddr.port}" if c.raddr else "",
                "status": c.status,
                "pid": c.pid,
            })
    except (psutil.AccessDenied, PermissionError):
        pass
    return conns


def _default_gateway_and_dns():
    system = platform.system()
    gateway, dns_servers = "", []
    if system == "Linux":
        out = _run(["ip", "route", "show", "default"])
        if out:
            parts = out.split()
            if "via" in parts:
                gateway = parts[parts.index("via") + 1]
        try:
            with open("/etc/resolv.conf") as f:
                dns_servers = [line.split()[1] for line in f if line.startswith("nameserver")]
        except Exception:
            pass
    elif system == "Windows":
        out = _run(["powershell", "-NoProfile", "-Command",
                     "(Get-NetRoute -DestinationPrefix '0.0.0.0/0').NextHop"])
        gateway = out.splitlines()[0].strip() if out else ""
        out = _run(["powershell", "-NoProfile", "-Command",
                     "(Get-DnsClientServerAddress -AddressFamily IPv4).ServerAddresses"])
        dns_servers = [l.strip() for l in out.splitlines() if l.strip()]
    elif system == "Darwin":
        out = _run(["route", "-n", "get", "default"])
        for line in out.splitlines():
            if "gateway:" in line:
                gateway = line.split(":", 1)[1].strip()
        out = _run(["scutil", "--dns"])
        dns_servers = sorted(set(
            line.split(":", 1)[1].strip()
            for line in out.splitlines() if line.strip().startswith("nameserver[")
        ))
    return gateway, dns_servers


def collect() -> dict:
    gateway, dns_servers = _default_gateway_and_dns()
    io = psutil.net_io_counters()
    return {
        "hostname": socket.gethostname(),
        "default_gateway": gateway,
        "dns_servers": dns_servers,
        "interfaces": _interfaces(),
        "connections": _connections(),
        "bytes_sent": io.bytes_sent,
        "bytes_recv": io.bytes_recv,
        "packets_sent": io.packets_sent,
        "packets_recv": io.packets_recv,
    }


if __name__ == "__main__":
    import json
    d = collect()
    print(json.dumps({**d, "connections": d["connections"][:5]}, indent=2, default=str))
