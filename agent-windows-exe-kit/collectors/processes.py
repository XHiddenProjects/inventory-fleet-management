"""Running process collector. psutil handles Linux/Windows/macOS uniformly,
so there's no per-OS branching needed here."""
import psutil


def collect(limit: int = 500) -> dict:
    procs = []
    for p in psutil.process_iter([
        "pid", "ppid", "name", "username", "status", "create_time",
        "cpu_percent", "memory_percent", "num_threads",
    ]):
        try:
            info = p.info
            try:
                exe = p.exe()
            except (psutil.AccessDenied, psutil.ZombieProcess, FileNotFoundError):
                exe = ""
            try:
                cmdline = " ".join(p.cmdline())
            except (psutil.AccessDenied, psutil.ZombieProcess):
                cmdline = ""
            try:
                mem = p.memory_info()
                rss = mem.rss
            except (psutil.AccessDenied, psutil.ZombieProcess, psutil.NoSuchProcess):
                rss = None
            procs.append({
                "pid": info["pid"],
                "ppid": info["ppid"],
                "name": info["name"],
                "exe": exe,
                "cmdline": cmdline[:500],
                "user": info["username"],
                "status": info["status"],
                "started_at": info["create_time"],
                "cpu_percent": info["cpu_percent"],
                "memory_percent": round(info["memory_percent"], 2) if info["memory_percent"] else 0,
                "memory_rss_bytes": rss,
                "num_threads": info["num_threads"],
            })
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            continue

    procs.sort(key=lambda x: (x["cpu_percent"] or 0), reverse=True)
    return {"count": len(procs), "processes": procs[:limit]}


if __name__ == "__main__":
    import json
    d = collect()
    print(json.dumps({"count": d["count"], "top5": d["processes"][:5]}, indent=2, default=str))
