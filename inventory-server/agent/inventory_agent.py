#!/usr/bin/env python3
"""
Inventory agent. Single-file entrypoint, cross-platform (Linux/Windows/macOS).

State (agent_id, token, server URL) is persisted to a small JSON file so
restarts don't re-enroll. Each cycle: collect all inventory categories,
POST them in one checkin request (which also returns any jobs the server
wants this agent to run), execute anything returned, and report results on
the *next* checkin (simpler and more robust than a separate result POST
mid-cycle, though /api/jobs/pending + a same-cycle result POST also both
exist server-side for lower-latency job dispatch if wanted later).
"""
import argparse
import hashlib
import json
import os
import platform
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import traceback
import uuid
from pathlib import Path
from urllib import request as urlrequest
from urllib.error import HTTPError, URLError

AGENT_VERSION = "0.1.0"

COLLECTORS = ["system", "software", "processes", "network", "identity", "services"]


def _state_dir() -> Path:
    system = platform.system()
    if system == "Windows":
        base = Path(os.environ.get("ProgramData", "C:/ProgramData"))
    elif system == "Darwin":
        base = Path("/Library/Application Support")
    else:
        base = Path("/var/lib")
    return base / "inventory-agent"


def load_state(state_path: Path) -> dict:
    if state_path.exists():
        try:
            return json.loads(state_path.read_text())
        except Exception:
            pass
    return {}


def save_state(state_path: Path, state: dict):
    state_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = state_path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2))
    tmp.replace(state_path)


def http_json(url: str, payload: dict | None = None, token: str | None = None, method: str = "POST", timeout: int = 30) -> dict:
    data = json.dumps(payload).encode() if payload is not None else None
    req = urlrequest.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    with urlrequest.urlopen(req, timeout=timeout) as resp:
        body = resp.read()
        return json.loads(body) if body else {}


def download_file(url: str, token: str, dest_path: str, timeout: int = 600) -> None:
    """Streams a file to disk with the agent's own bearer token. Used for
    approved-file (silent install) jobs - never for arbitrary URLs supplied
    by anything other than the server's own job payload."""
    req = urlrequest.Request(url, method="GET")
    req.add_header("Authorization", f"Bearer {token}")
    with urlrequest.urlopen(req, timeout=timeout) as resp, open(dest_path, "wb") as f:
        while True:
            chunk = resp.read(1024 * 1024)
            if not chunk:
                break
            f.write(chunk)


def collect_all() -> dict:
    inventory = {}
    for name in COLLECTORS:
        try:
            mod = __import__(f"collectors.{name}", fromlist=["collect"])
            inventory[name] = mod.collect()
        except Exception:
            inventory[name] = {"error": traceback.format_exc(limit=3)}
    return inventory


def run_job(job: dict, state: dict) -> dict:
    """Dispatches one job to the right executor based on its job_type and
    returns a job_results entry. 'script' jobs (the original behavior) run
    an inline script; 'file_install' jobs fetch an admin-approved installer
    and run it silently/non-interactively."""
    if job.get("job_type") == "file_install":
        return run_file_install_job(job, state)
    return run_script_job(job)


def run_script_job(job: dict) -> dict:
    """Executes one job's script and returns a job_results entry."""
    interpreter = job.get("interpreter", "auto")
    system = platform.system()
    if interpreter == "auto":
        interpreter = "powershell" if system == "Windows" else "bash"

    suffix = {"bash": ".sh", "powershell": ".ps1", "python": ".py"}.get(interpreter, ".sh")
    with tempfile.NamedTemporaryFile("w", suffix=suffix, delete=False) as f:
        f.write(job["script"])
        script_path = f.name

    if interpreter == "powershell":
        cmd = ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", script_path]
    elif interpreter == "python":
        cmd = [sys.executable, script_path]
    else:
        cmd = ["bash", script_path] if shutil_which("bash") else ["sh", script_path]

    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=job.get("timeout_seconds", 300))
        status = "success" if proc.returncode == 0 else "failed"
        return {
            "job_id": job["id"], "status": status, "exit_code": proc.returncode,
            "stdout": proc.stdout[-20000:], "stderr": proc.stderr[-20000:],
        }
    except subprocess.TimeoutExpired as e:
        return {
            "job_id": job["id"], "status": "timeout", "exit_code": None,
            "stdout": (e.stdout or "")[-20000:] if isinstance(e.stdout, str) else "",
            "stderr": f"Timed out after {job.get('timeout_seconds', 300)}s",
        }
    except Exception:
        return {"job_id": job["id"], "status": "failed", "exit_code": None, "stdout": "", "stderr": traceback.format_exc(limit=5)}
    finally:
        try:
            os.unlink(script_path)
        except OSError:
            pass


def _install_command_for(file_path: str, install_args: str) -> list[str] | None:
    """Builds the silent/non-interactive install command for a downloaded
    file, based on its extension and the current OS. Returns None if this
    file type isn't installable on this platform (the job is marked failed
    rather than guessed at)."""
    ext = Path(file_path).suffix.lower()
    system = platform.system()
    args = install_args.split() if install_args else []

    if system == "Windows":
        if ext == ".msi":
            return ["msiexec", "/i", file_path] + (args or ["/quiet", "/norestart"])
        if ext in (".msp",):
            return ["msiexec", "/p", file_path] + (args or ["/quiet", "/norestart"])
        if ext == ".exe":
            return [file_path] + (args or ["/S"])
    elif system == "Darwin":
        if ext == ".pkg":
            return ["installer", "-pkg", file_path, "-target", "/"] + args
        if ext in (".sh", ".run"):
            return ["bash", file_path] + args
    else:  # Linux
        if ext == ".deb":
            return ["dpkg", "-i", file_path] + args
        if ext == ".rpm":
            rpm_cmd = "dnf" if shutil_which("dnf") else ("yum" if shutil_which("yum") else "rpm")
            return ([rpm_cmd, "install", "-y", file_path] if rpm_cmd != "rpm" else ["rpm", "-Uvh", file_path]) + args
        if ext in (".sh", ".run"):
            return ["bash", file_path] + args
    return None


def run_file_install_job(job: dict, state: dict) -> dict:
    """Downloads an admin-approved file from the server and installs it
    silently/non-interactively in the background (no UI shown to the
    logged-in user). The file is verified against the sha256 the server
    recorded at upload time before anything is executed."""
    job_id = job["id"]
    file_name = job.get("file_name") or f"{job.get('file_id', 'file')}.bin"
    expected_sha256 = job.get("file_sha256")
    args = (job.get("install_args") or "").strip() or (job.get("file_default_silent_args") or "")

    tmp_dir = tempfile.mkdtemp(prefix="inv-install-")
    dest = os.path.join(tmp_dir, os.path.basename(file_name))
    try:
        url = f"{state['server']}/api/agent-files/{job['file_id']}"
        download_file(url, state["token"], dest, timeout=max(600, job.get("timeout_seconds", 300)))

        if expected_sha256:
            actual = hashlib.sha256()
            with open(dest, "rb") as f:
                for chunk in iter(lambda: f.read(1024 * 1024), b""):
                    actual.update(chunk)
            if actual.hexdigest().lower() != expected_sha256.lower():
                return {"job_id": job_id, "status": "failed", "exit_code": None, "stdout": "",
                         "stderr": "Downloaded file did not match the checksum recorded on the server; refusing to run it."}

        if platform.system() != "Windows":
            os.chmod(dest, 0o755)

        cmd = _install_command_for(dest, args)
        if cmd is None:
            return {"job_id": job_id, "status": "failed", "exit_code": None, "stdout": "",
                     "stderr": f"Don't know how to silently install a '{Path(dest).suffix}' file on {platform.system()}."}

        kwargs = {}
        if platform.system() == "Windows":
            # No console window / UI pops up for whoever is logged in.
            si = subprocess.STARTUPINFO()
            si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            kwargs["startupinfo"] = si
            kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)

        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=job.get("timeout_seconds", 300), **kwargs)
        status = "success" if proc.returncode == 0 else "failed"
        return {
            "job_id": job_id, "status": status, "exit_code": proc.returncode,
            "stdout": proc.stdout[-20000:], "stderr": proc.stderr[-20000:],
        }
    except subprocess.TimeoutExpired as e:
        return {
            "job_id": job_id, "status": "timeout", "exit_code": None,
            "stdout": (e.stdout or "")[-20000:] if isinstance(e.stdout, str) else "",
            "stderr": f"Timed out after {job.get('timeout_seconds', 300)}s",
        }
    except (HTTPError, URLError) as e:
        return {"job_id": job_id, "status": "failed", "exit_code": None, "stdout": "", "stderr": f"Download failed: {e}"}
    except Exception:
        return {"job_id": job_id, "status": "failed", "exit_code": None, "stdout": "", "stderr": traceback.format_exc(limit=5)}
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def shutil_which(name):
    return shutil.which(name)


def enroll(server: str, enrollment_key: str, state_path: Path) -> dict:
    system_key = {"Linux": "linux", "Windows": "windows", "Darwin": "macos"}[platform.system()]
    payload = {
        "enrollment_key": enrollment_key,
        "hostname": socket.gethostname(),
        "os": system_key,
        "os_version": platform.version(),
        "arch": platform.machine(),
        "agent_version": AGENT_VERSION,
        "agent_id": str(uuid.uuid4()),
    }
    resp = http_json(f"{server}/api/enroll", payload)
    state = {"server": server, "agent_id": resp["agent_id"], "token": resp["token"]}
    save_state(state_path, state)
    print(f"Enrolled as agent {state['agent_id']} against {server}")
    return state


def run_once(state: dict, pending_results: list) -> list:
    """Runs one collect+checkin cycle. Returns the new pending_results list
    to persist: [] if the checkin succeeded (results were delivered and any
    newly-assigned jobs were executed), unchanged if it failed (so nothing
    is lost and they're retried next cycle)."""
    inventory = collect_all()
    resp = http_json(
        f"{state['server']}/api/checkin/{state['agent_id']}",
        {"inventory": inventory, "job_results": pending_results},
        token=state["token"],
    )
    # Checkin succeeded: previously-pending results are now delivered.
    new_results = [run_job(job, state) for job in resp.get("jobs", [])]
    return new_results


def main():
    ap = argparse.ArgumentParser(description="Inventory agent")
    ap.add_argument("--server", help="Server base URL, e.g. http://inventory.example.com:8787")
    ap.add_argument("--enrollment-key", help="Enrollment key (required for first run only)")
    ap.add_argument("--interval", type=int, default=60, help="Seconds between check-ins")
    ap.add_argument("--once", action="store_true", help="Run a single collect+checkin cycle and exit")
    ap.add_argument("--state-file", default=str(_state_dir() / "state.json"))
    ap.add_argument("--log-file", help="Append agent output to this file")
    ap.add_argument("--print-only", action="store_true", help="Collect and print inventory locally; don't contact a server")
    args = ap.parse_args()

    if args.print_only:
        print(json.dumps(collect_all(), indent=2, default=str))
        return

    if args.log_file:
        log_path = Path(args.log_file)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        if log_path.exists() and log_path.stat().st_size >= 1024 * 1024:
            previous_log = log_path.with_suffix(log_path.suffix + ".1")
            previous_log.unlink(missing_ok=True)
            log_path.replace(previous_log)
        log_stream = log_path.open("a", encoding="utf-8", buffering=1)
        sys.stdout = log_stream
        sys.stderr = log_stream

    state_path = Path(args.state_file)
    state = load_state(state_path)

    if "agent_id" not in state:
        if not args.server or not args.enrollment_key:
            print("Not enrolled yet: --server and --enrollment-key are required on first run.", file=sys.stderr)
            sys.exit(2)
        state = enroll(args.server, args.enrollment_key, state_path)
    elif args.server:
        state["server"] = args.server

    pending_results = state.pop("_pending_results", [])
    while True:
        cycle_failed = False
        try:
            new_pending = run_once(state, pending_results)
            pending_results = new_pending
            save_state(state_path, {**state, "_pending_results": pending_results})
            print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] checkin ok"
                  + (f", ran {len(pending_results)} job(s)" if pending_results else ""))
        except HTTPError as e:
            print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] checkin failed: HTTP {e.code} {e.reason}", file=sys.stderr)
            cycle_failed = True
        except URLError as e:
            print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] checkin failed: {e.reason}", file=sys.stderr)
            cycle_failed = True
        except Exception:
            print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] checkin failed:\n{traceback.format_exc(limit=5)}", file=sys.stderr)
            cycle_failed = True

        if args.once:
            if cycle_failed:
                sys.exit(1)
            break
        time.sleep(args.interval)


if __name__ == "__main__":
    main()
