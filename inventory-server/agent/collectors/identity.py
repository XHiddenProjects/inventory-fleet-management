"""Local users & groups collector. Genuinely per-OS: Linux/macOS have
pwd/grp, Windows has none of that and needs PowerShell/WMI instead."""
import platform
import subprocess


def _run(cmd, timeout=15):
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return out.stdout
    except Exception:
        return ""


def _linux_or_macos():
    import grp
    import pwd

    users = []
    for p in pwd.getpwall():
        users.append({
            "username": p.pw_name,
            "uid": p.pw_uid,
            "gid": p.pw_gid,
            "full_name": (p.pw_gecos or "").split(",")[0],
            "home": p.pw_dir,
            "shell": p.pw_shell,
            "is_system": p.pw_uid < 1000,
        })

    groups = []
    for g in grp.getgrall():
        groups.append({
            "name": g.gr_name,
            "gid": g.gr_gid,
            "members": list(g.gr_mem),
        })

    return users, groups


def _windows():
    ps_users = (
        "Get-LocalUser | Select-Object Name, SID, Enabled, FullName, Description, LastLogon | "
        "ForEach-Object { \"$($_.Name)`t$($_.SID)`t$($_.Enabled)`t$($_.FullName)`t$($_.Description)`t$($_.LastLogon)\" }"
    )
    out = _run(["powershell", "-NoProfile", "-Command", ps_users])
    users = []
    for line in out.splitlines():
        parts = line.split("\t")
        if len(parts) < 3:
            continue
        name, sid, enabled = parts[0], parts[1], parts[2]
        full_name = parts[3] if len(parts) > 3 else ""
        description = parts[4] if len(parts) > 4 else ""
        users.append({
            "username": name, "uid": sid, "gid": "",
            "full_name": full_name, "home": "", "shell": "",
            "is_system": False, "enabled": enabled.strip().lower() == "true",
            "description": description,
        })

    ps_groups = (
        "Get-LocalGroup | ForEach-Object { "
        "$g = $_; $members = (Get-LocalGroupMember $g.Name -ErrorAction SilentlyContinue | "
        "ForEach-Object { $_.Name }) -join ';'; "
        "\"$($g.Name)`t$($g.SID)`t$members\" }"
    )
    out = _run(["powershell", "-NoProfile", "-Command", ps_groups], timeout=30)
    groups = []
    for line in out.splitlines():
        parts = line.split("\t")
        if len(parts) < 2:
            continue
        name, sid = parts[0], parts[1]
        members = parts[2].split(";") if len(parts) > 2 and parts[2] else []
        groups.append({"name": name, "gid": sid, "members": members})

    return users, groups


def collect() -> dict:
    system = platform.system()
    if system in ("Linux", "Darwin"):
        users, groups = _linux_or_macos()
    elif system == "Windows":
        users, groups = _windows()
    else:
        users, groups = [], []
    return {
        "users": users,
        "groups": groups,
        "user_count": len(users),
        "group_count": len(groups),
    }


if __name__ == "__main__":
    import json
    d = collect()
    print(json.dumps({
        "user_count": d["user_count"], "group_count": d["group_count"],
        "sample_users": d["users"][:5], "sample_groups": d["groups"][:5],
    }, indent=2, default=str))
