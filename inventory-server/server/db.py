"""
SQLite storage layer for the inventory server.

Deliberately plain sqlite3 (no ORM) so the whole server has exactly one
runtime dependency surface (FastAPI/uvicorn/pydantic) and the database file
can be read directly by the Cockpit-side CLI helper without going through
the HTTP API at all.
"""
import json
import os
import secrets
import sqlite3
import time
import hashlib
from contextlib import contextmanager
from pathlib import Path

DB_PATH = os.environ.get("INVENTORY_DB", "/var/lib/inventory-server/inventory.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS agents (
    id              TEXT PRIMARY KEY,
    hostname        TEXT NOT NULL,
    os              TEXT NOT NULL,          -- linux | windows | macos
    os_version      TEXT,
    arch            TEXT,
    agent_version   TEXT,
    token_hash      TEXT NOT NULL,
    enrolled_at     REAL NOT NULL,
    last_seen       REAL,
    last_ip         TEXT,
    tags            TEXT DEFAULT '[]',      -- JSON array, user-assignable labels
    revoked         INTEGER NOT NULL DEFAULT 0
);

-- One row per (agent, category) holding the latest snapshot as JSON.
-- Categories: system, software, processes, network, identity, services
CREATE TABLE IF NOT EXISTS inventory (
    agent_id    TEXT NOT NULL,
    category    TEXT NOT NULL,
    collected_at REAL NOT NULL,
    data        TEXT NOT NULL,              -- JSON
    PRIMARY KEY (agent_id, category),
    FOREIGN KEY (agent_id) REFERENCES agents(id)
);

-- Append-only history of check-ins, trimmed periodically, used for
-- "agents seen over time" / staleness detection and simple sparklines.
CREATE TABLE IF NOT EXISTS checkins (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    agent_id    TEXT NOT NULL,
    at          REAL NOT NULL,
    ip          TEXT
);
CREATE INDEX IF NOT EXISTS idx_checkins_agent_at ON checkins(agent_id, at);

CREATE TABLE IF NOT EXISTS jobs (
    id              TEXT PRIMARY KEY,
    created_at      REAL NOT NULL,
    created_by      TEXT,
    name            TEXT,
    job_type        TEXT NOT NULL DEFAULT 'script',  -- script | file_install
    script          TEXT,                            -- required when job_type='script'
    interpreter     TEXT NOT NULL DEFAULT 'auto',     -- auto | bash | powershell | python
    file_id         TEXT,                             -- required when job_type='file_install'
    install_args    TEXT NOT NULL DEFAULT '',          -- extra silent-install args
    target_agent_ids TEXT NOT NULL,                -- JSON array; empty array = all agents
    timeout_seconds INTEGER NOT NULL DEFAULT 300
);

-- Executables/installers the admin has uploaded and approved to be pushed
-- to agents via a file_install job. Only admins (the one INVENTORY_ADMIN_TOKEN)
-- can add or remove entries here - agents can only ever fetch a file that's
-- already listed in this table, over an authenticated job they were assigned.
CREATE TABLE IF NOT EXISTS approved_files (
    id              TEXT PRIMARY KEY,
    label           TEXT NOT NULL DEFAULT '',
    original_filename TEXT NOT NULL,
    stored_path     TEXT NOT NULL,
    platform        TEXT NOT NULL DEFAULT 'any',   -- windows | macos | linux | any
    sha256          TEXT NOT NULL,
    size_bytes      INTEGER NOT NULL,
    default_silent_args TEXT NOT NULL DEFAULT '',
    uploaded_at     REAL NOT NULL,
    uploaded_by     TEXT DEFAULT ''
);

CREATE TABLE IF NOT EXISTS job_results (
    job_id      TEXT NOT NULL,
    agent_id    TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'pending',  -- pending | running | success | failed | timeout
    exit_code   INTEGER,
    stdout      TEXT,
    stderr      TEXT,
    started_at  REAL,
    finished_at REAL,
    PRIMARY KEY (job_id, agent_id),
    FOREIGN KEY (job_id) REFERENCES jobs(id)
);

CREATE TABLE IF NOT EXISTS enrollment_keys (
    key_hash    TEXT PRIMARY KEY,
    label       TEXT,
    created_at  REAL NOT NULL,
    expires_at  REAL,
    max_uses    INTEGER,
    uses        INTEGER NOT NULL DEFAULT 0,
    revoked     INTEGER NOT NULL DEFAULT 0
);
"""


def _ensure_parent_dir():
    Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)


@contextmanager
def get_conn():
    _ensure_parent_dir()
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def _migrate_jobs_table(conn):
    """Older databases may predate the job_type/file_id/install_args
    columns and the 'script NOT NULL' relaxation; add what's missing
    rather than forcing a destructive re-create."""
    cols = {row["name"] for row in conn.execute("PRAGMA table_info(jobs)")}
    if "job_type" not in cols:
        conn.execute("ALTER TABLE jobs ADD COLUMN job_type TEXT NOT NULL DEFAULT 'script'")
    if "file_id" not in cols:
        conn.execute("ALTER TABLE jobs ADD COLUMN file_id TEXT")
    if "install_args" not in cols:
        conn.execute("ALTER TABLE jobs ADD COLUMN install_args TEXT NOT NULL DEFAULT ''")


def init_db():
    with get_conn() as conn:
        conn.executescript(SCHEMA)
        _migrate_jobs_table(conn)


def hash_secret(secret: str) -> str:
    return hashlib.sha256(secret.encode()).hexdigest()


def new_token() -> str:
    return secrets.token_urlsafe(32)


# ---------------- enrollment keys ----------------

def create_enrollment_key(label: str = "", expires_in_days: int | None = None, max_uses: int | None = None) -> str:
    raw = "enr_" + secrets.token_urlsafe(24)
    expires_at = time.time() + expires_in_days * 86400 if expires_in_days else None
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO enrollment_keys (key_hash, label, created_at, expires_at, max_uses) VALUES (?,?,?,?,?)",
            (hash_secret(raw), label, time.time(), expires_at, max_uses),
        )
    return raw


def validate_enrollment_key(raw: str) -> bool:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM enrollment_keys WHERE key_hash=?", (hash_secret(raw),)
        ).fetchone()
        if not row or row["revoked"]:
            return False
        if row["expires_at"] and time.time() > row["expires_at"]:
            return False
        if row["max_uses"] is not None and row["uses"] >= row["max_uses"]:
            return False
        conn.execute("UPDATE enrollment_keys SET uses = uses + 1 WHERE key_hash=?", (hash_secret(raw),))
        return True


def list_enrollment_keys():
    with get_conn() as conn:
        return [dict(r) for r in conn.execute("SELECT key_hash, label, created_at, expires_at, max_uses, uses, revoked FROM enrollment_keys")]


def delete_enrollment_key(key_hash: str) -> bool:
    """Hard-deletes an enrollment key. Deleting a key only stops it from
    being used for *future* enrollments - agents already enrolled with it
    keep their own per-agent token and are unaffected."""
    with get_conn() as conn:
        cur = conn.execute("DELETE FROM enrollment_keys WHERE key_hash=?", (key_hash,))
        return cur.rowcount > 0


# ---------------- agents ----------------

def enroll_agent(agent_id: str, hostname: str, os_name: str, os_version: str, arch: str, agent_version: str, ip: str) -> str:
    token = new_token()
    now = time.time()
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO agents (id, hostname, os, os_version, arch, agent_version, token_hash, enrolled_at, last_seen, last_ip)
               VALUES (?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(id) DO UPDATE SET
                 hostname=excluded.hostname, os=excluded.os, os_version=excluded.os_version,
                 arch=excluded.arch, agent_version=excluded.agent_version, token_hash=excluded.token_hash,
                 last_seen=excluded.last_seen, last_ip=excluded.last_ip, revoked=0""",
            (agent_id, hostname, os_name, os_version, arch, agent_version, hash_secret(token), now, now, ip),
        )
    return token


def check_agent_token(agent_id: str, token: str) -> bool:
    with get_conn() as conn:
        row = conn.execute("SELECT token_hash, revoked FROM agents WHERE id=?", (agent_id,)).fetchone()
        return bool(row) and not row["revoked"] and row["token_hash"] == hash_secret(token)


def get_agent_by_token(token: str):
    """Looks up an agent by its bearer token alone (no agent_id known yet),
    for endpoints like the approved-file download that aren't nested under
    /agents/{agent_id}. Revoked agents never match."""
    token_hash = hash_secret(token)
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM agents WHERE token_hash=? AND revoked=0", (token_hash,)).fetchone()
        return dict(row) if row else None


def touch_agent(agent_id: str, ip: str):
    now = time.time()
    with get_conn() as conn:
        conn.execute("UPDATE agents SET last_seen=?, last_ip=? WHERE id=?", (now, ip, agent_id))
        conn.execute("INSERT INTO checkins (agent_id, at, ip) VALUES (?,?,?)", (agent_id, now, ip))
        # keep history bounded
        conn.execute(
            "DELETE FROM checkins WHERE agent_id=? AND id NOT IN (SELECT id FROM checkins WHERE agent_id=? ORDER BY at DESC LIMIT 500)",
            (agent_id, agent_id),
        )


def save_inventory(agent_id: str, category: str, data: dict):
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO inventory (agent_id, category, collected_at, data) VALUES (?,?,?,?)
               ON CONFLICT(agent_id, category) DO UPDATE SET collected_at=excluded.collected_at, data=excluded.data""",
            (agent_id, category, time.time(), json.dumps(data)),
        )


def get_inventory(agent_id: str, category: str | None = None) -> dict:
    with get_conn() as conn:
        if category:
            row = conn.execute(
                "SELECT collected_at, data FROM inventory WHERE agent_id=? AND category=?", (agent_id, category)
            ).fetchone()
            return {"collected_at": row["collected_at"], "data": json.loads(row["data"])} if row else {}
        rows = conn.execute("SELECT category, collected_at, data FROM inventory WHERE agent_id=?", (agent_id,)).fetchall()
        return {r["category"]: {"collected_at": r["collected_at"], "data": json.loads(r["data"])} for r in rows}


def list_agents(online_within_seconds: int = 300):
    now = time.time()
    with get_conn() as conn:
        rows = conn.execute("SELECT * FROM agents WHERE revoked=0 ORDER BY hostname").fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["tags"] = json.loads(d.get("tags") or "[]")
            d["online"] = bool(d["last_seen"] and (now - d["last_seen"]) <= online_within_seconds)
            out.append(d)
        return out


def get_agent(agent_id: str):
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM agents WHERE id=?", (agent_id,)).fetchone()
        if not row:
            return None
        d = dict(row)
        d["tags"] = json.loads(d.get("tags") or "[]")
        return d


def revoke_agent(agent_id: str):
    with get_conn() as conn:
        conn.execute("UPDATE agents SET revoked=1 WHERE id=?", (agent_id,))


def delete_agent(agent_id: str):
    with get_conn() as conn:
        conn.execute("DELETE FROM inventory WHERE agent_id=?", (agent_id,))
        conn.execute("DELETE FROM checkins WHERE agent_id=?", (agent_id,))
        conn.execute("DELETE FROM job_results WHERE agent_id=?", (agent_id,))
        conn.execute("DELETE FROM agents WHERE id=?", (agent_id,))


def set_agent_tags(agent_id: str, tags: list):
    with get_conn() as conn:
        conn.execute("UPDATE agents SET tags=? WHERE id=?", (json.dumps(tags), agent_id))


# ---------------- jobs ----------------

def create_job(job_id: str, name: str, interpreter: str, target_agent_ids: list, timeout_seconds: int,
                created_by: str = "", job_type: str = "script", script: str | None = None,
                file_id: str | None = None, install_args: str = ""):
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO jobs (id, created_at, created_by, name, job_type, script, interpreter, file_id, install_args, target_agent_ids, timeout_seconds)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (job_id, time.time(), created_by, name, job_type, script, interpreter, file_id, install_args,
             json.dumps(target_agent_ids), timeout_seconds),
        )
        targets = target_agent_ids or [a["id"] for a in list_agents(online_within_seconds=10**9)]
        for agent_id in targets:
            conn.execute(
                "INSERT INTO job_results (job_id, agent_id, status) VALUES (?,?, 'pending')",
                (job_id, agent_id),
            )


def get_pending_jobs_for_agent(agent_id: str):
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT j.id, j.name, j.job_type, j.script, j.interpreter, j.file_id, j.install_args, j.timeout_seconds,
                      f.original_filename AS file_name, f.sha256 AS file_sha256, f.size_bytes AS file_size_bytes,
                      f.default_silent_args AS file_default_silent_args
               FROM jobs j JOIN job_results r ON r.job_id = j.id
               LEFT JOIN approved_files f ON f.id = j.file_id
               WHERE r.agent_id=? AND r.status='pending'""",
            (agent_id,),
        ).fetchall()
        return [dict(r) for r in rows]


def mark_job_running(job_id: str, agent_id: str):
    with get_conn() as conn:
        conn.execute(
            "UPDATE job_results SET status='running', started_at=? WHERE job_id=? AND agent_id=?",
            (time.time(), job_id, agent_id),
        )


def save_job_result(job_id: str, agent_id: str, status: str, exit_code, stdout: str, stderr: str):
    with get_conn() as conn:
        conn.execute(
            """UPDATE job_results SET status=?, exit_code=?, stdout=?, stderr=?, finished_at=?
               WHERE job_id=? AND agent_id=?""",
            (status, exit_code, stdout, stderr, time.time(), job_id, agent_id),
        )


def _decorate_job(conn, j) -> dict:
    d = dict(j)
    d["target_agent_ids"] = json.loads(d["target_agent_ids"])
    if d.get("job_type") == "file_install" and d.get("file_id"):
        f = conn.execute(
            "SELECT original_filename, label, platform, size_bytes FROM approved_files WHERE id=?",
            (d["file_id"],),
        ).fetchone()
        d["file"] = dict(f) if f else None
    results = conn.execute("SELECT * FROM job_results WHERE job_id=?", (d["id"],)).fetchall()
    d["results"] = [dict(r) for r in results]
    return d


def list_jobs():
    with get_conn() as conn:
        jobs = conn.execute("SELECT * FROM jobs ORDER BY created_at DESC").fetchall()
        return [_decorate_job(conn, j) for j in jobs]


def get_job(job_id: str):
    with get_conn() as conn:
        j = conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        return _decorate_job(conn, j) if j else None


# ---------------- approved files (silent installs) ----------------

def create_approved_file(file_id: str, label: str, original_filename: str, stored_path: str, platform: str,
                          sha256: str, size_bytes: int, default_silent_args: str, uploaded_by: str = ""):
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO approved_files (id, label, original_filename, stored_path, platform, sha256,
                                            size_bytes, default_silent_args, uploaded_at, uploaded_by)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (file_id, label, original_filename, stored_path, platform, sha256, size_bytes,
             default_silent_args, time.time(), uploaded_by),
        )


def list_approved_files():
    with get_conn() as conn:
        return [dict(r) for r in conn.execute(
            "SELECT id, label, original_filename, platform, sha256, size_bytes, default_silent_args, uploaded_at, uploaded_by "
            "FROM approved_files ORDER BY uploaded_at DESC"
        )]


def get_approved_file(file_id: str):
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM approved_files WHERE id=?", (file_id,)).fetchone()
        return dict(row) if row else None


def delete_approved_file(file_id: str) -> bool:
    with get_conn() as conn:
        cur = conn.execute("DELETE FROM approved_files WHERE id=?", (file_id,))
        return cur.rowcount > 0


# ---------------- dashboard ----------------

def dashboard_summary(online_within_seconds: int = 300):
    agents = list_agents(online_within_seconds=online_within_seconds)
    os_counts = {}
    for a in agents:
        os_counts[a["os"]] = os_counts.get(a["os"], 0) + 1
    online = sum(1 for a in agents if a["online"])
    with get_conn() as conn:
        total_software = conn.execute(
            "SELECT agent_id, data FROM inventory WHERE category='software'"
        ).fetchall()
    software_pkg_total = 0
    for row in total_software:
        try:
            software_pkg_total += len(json.loads(row["data"]).get("packages", []))
        except Exception:
            pass
    return {
        "total_agents": len(agents),
        "online": online,
        "offline": len(agents) - online,
        "os_breakdown": os_counts,
        "software_packages_tracked": software_pkg_total,
    }
