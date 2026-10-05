"""
Inventory server API.

Two audiences hit this API:
  - Agents (Linux/Windows/macOS): POST /api/enroll, /api/checkin/{id}, poll
    /api/jobs/pending/{id}, POST /api/jobs/{id}/result/{agent_id}.
    Authenticated with a per-agent bearer token issued at enrollment.
  - The admin/Cockpit side: everything under /api/admin/*.
    Authenticated with a single admin bearer token (INVENTORY_ADMIN_TOKEN).

Deliberately not multi-tenant, not clustered, not doing TLS termination
itself (put it behind a reverse proxy or run it on a trusted management
network) - this is a first version, not a hardened enterprise product.
"""
import hashlib
import os
import secrets
import time
import uuid
from typing import Literal
from pathlib import Path

from fastapi import FastAPI, HTTPException, Header, Depends, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import PlainTextResponse, FileResponse
from pydantic import BaseModel, Field, field_validator, model_validator

from . import db

app = FastAPI(title="Inventory Server", version="0.1.0")

# Permissive CORS: this API is meant to be reachable from a Cockpit page
# served on a different port/origin on the same trusted host/network.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

AGENT_DIR = Path(__file__).resolve().parent.parent / "agent"
INSTALL_SCRIPTS_DIR = Path(__file__).resolve().parent / "install_scripts"

if AGENT_DIR.is_dir():
    app.mount("/agent/files", StaticFiles(directory=str(AGENT_DIR)), name="agent-files")

ADMIN_TOKEN = os.environ.get("INVENTORY_ADMIN_TOKEN")
ONLINE_WINDOW_SECONDS = int(os.environ.get("INVENTORY_ONLINE_WINDOW", "300"))

# Where admin-uploaded, approved installers/executables are stored on disk.
# Separate from AGENT_DIR (the agent's own source, served unauthenticated)
# because these downloads require a valid agent bearer token - see
# GET /api/agent-files/{file_id} below.
APPROVED_FILES_DIR = Path(os.environ.get("INVENTORY_FILES_DIR", str(Path(db.DB_PATH).resolve().parent / "approved-files")))
MAX_APPROVED_FILE_BYTES = int(os.environ.get("INVENTORY_MAX_FILE_BYTES", str(1024 * 1024 * 1024)))  # 1 GiB default


@app.on_event("startup")
def _startup():
    db.init_db()
    global ADMIN_TOKEN
    if not ADMIN_TOKEN:
        # Dev convenience only: generate and print one so the server is
        # usable out of the box. Production installs should set
        # INVENTORY_ADMIN_TOKEN explicitly (the install script does this).
        ADMIN_TOKEN = secrets.token_urlsafe(24)
        print(f"[inventory-server] No INVENTORY_ADMIN_TOKEN set - generated one for this run: {ADMIN_TOKEN}")


def require_admin(authorization: str = Header(default="")):
    token = authorization.removeprefix("Bearer ").strip()
    if not token or token != ADMIN_TOKEN:
        raise HTTPException(401, "Invalid or missing admin token")


def require_agent(agent_id: str, authorization: str = Header(default="")):
    token = authorization.removeprefix("Bearer ").strip()
    if not token or not db.check_agent_token(agent_id, token):
        raise HTTPException(401, "Invalid or missing agent token")


def require_any_agent(authorization: str = Header(default="")) -> dict:
    """Like require_agent, but for endpoints not nested under a known
    /agents/{agent_id} path (the approved-file download). Accepts any
    currently-valid, non-revoked agent token and returns that agent's row."""
    token = authorization.removeprefix("Bearer ").strip()
    agent = db.get_agent_by_token(token) if token else None
    if not agent:
        raise HTTPException(401, "Invalid or missing agent token")
    return agent


# ---------------- schemas ----------------

class EnrollRequest(BaseModel):
    enrollment_key: str
    hostname: str
    os: str = Field(pattern="^(linux|windows|macos)$")
    os_version: str = ""
    arch: str = ""
    agent_version: str = ""
    agent_id: str | None = None  # stable id persisted by the agent across restarts


class CheckinRequest(BaseModel):
    inventory: dict = {}          # { category: {...data...} }
    job_results: list = []        # [{job_id, status, exit_code, stdout, stderr}]


class CreateJobRequest(BaseModel):
    name: str = ""
    job_type: Literal["script", "file_install"] = "script"
    script: str | None = None                 # required when job_type == "script"
    interpreter: Literal["auto", "bash", "powershell", "python"] = "auto"
    file_id: str | None = None                # required when job_type == "file_install"
    install_args: str = ""                    # extra args appended to the file's default silent-install args
    target_agent_ids: list[str] = Field(default_factory=list, max_length=1000)
    timeout_seconds: int = Field(default=300, ge=5, le=3600)

    @field_validator("script")
    @classmethod
    def limit_script_size(cls, script: str | None) -> str | None:
        if script is None:
            return script
        if len(script.encode("utf-8")) > 256 * 1024:
            raise ValueError("script must not exceed 256 KiB")
        return script

    @field_validator("install_args")
    @classmethod
    def limit_install_args(cls, install_args: str) -> str:
        if len(install_args) > 2000:
            raise ValueError("install_args must not exceed 2000 characters")
        return install_args

    @model_validator(mode="after")
    def check_type_specific_fields(self):
        if self.job_type == "script":
            if not self.script or not self.script.strip():
                raise ValueError("script must not be blank for a script job")
        elif self.job_type == "file_install":
            if not self.file_id:
                raise ValueError("file_id is required for a file_install job")
        return self


class CreateEnrollmentKeyRequest(BaseModel):
    label: str = ""
    expires_in_days: int | None = 30
    max_uses: int | None = None


class TagsRequest(BaseModel):
    tags: list[str]


# ---------------- agent-facing endpoints ----------------

@app.post("/api/enroll")
def enroll(req: EnrollRequest, x_forwarded_for: str = Header(default="")):
    if not db.validate_enrollment_key(req.enrollment_key):
        raise HTTPException(403, "Invalid, expired, or exhausted enrollment key")
    agent_id = req.agent_id or str(uuid.uuid4())
    token = db.enroll_agent(
        agent_id, req.hostname, req.os, req.os_version, req.arch, req.agent_version,
        x_forwarded_for or "",
    )
    return {"agent_id": agent_id, "token": token}


@app.post("/api/checkin/{agent_id}")
def checkin(agent_id: str, req: CheckinRequest, authorization: str = Header(default=""),
            x_forwarded_for: str = Header(default="")):
    require_agent(agent_id, authorization)
    db.touch_agent(agent_id, x_forwarded_for or "")
    for category, data in req.inventory.items():
        db.save_inventory(agent_id, category, data)
    for r in req.job_results:
        db.save_job_result(agent_id=agent_id, job_id=r["job_id"], status=r.get("status", "failed"),
                            exit_code=r.get("exit_code"), stdout=r.get("stdout", ""), stderr=r.get("stderr", ""))
    pending = db.get_pending_jobs_for_agent(agent_id)
    for p in pending:
        db.mark_job_running(p["id"], agent_id)
    return {"ok": True, "jobs": pending}


@app.get("/api/jobs/pending/{agent_id}")
def jobs_pending(agent_id: str, authorization: str = Header(default="")):
    require_agent(agent_id, authorization)
    pending = db.get_pending_jobs_for_agent(agent_id)
    for p in pending:
        db.mark_job_running(p["id"], agent_id)
    return {"jobs": pending}


# ---------------- admin-facing endpoints ----------------

@app.get("/api/admin/dashboard", dependencies=[Depends(require_admin)])
def admin_dashboard():
    return db.dashboard_summary(ONLINE_WINDOW_SECONDS)


@app.get("/api/admin/agents", dependencies=[Depends(require_admin)])
def admin_agents():
    return {"agents": db.list_agents(ONLINE_WINDOW_SECONDS)}


@app.get("/api/admin/agents/{agent_id}", dependencies=[Depends(require_admin)])
def admin_agent_detail(agent_id: str):
    agent = db.get_agent(agent_id)
    if not agent:
        raise HTTPException(404, "No such agent")
    agent["online"] = bool(agent["last_seen"] and (time.time() - agent["last_seen"]) <= ONLINE_WINDOW_SECONDS)
    return {"agent": agent, "inventory": db.get_inventory(agent_id)}


@app.delete("/api/admin/agents/{agent_id}", dependencies=[Depends(require_admin)])
def admin_agent_delete(agent_id: str):
    db.delete_agent(agent_id)
    return {"ok": True}


@app.post("/api/admin/agents/{agent_id}/revoke", dependencies=[Depends(require_admin)])
def admin_agent_revoke(agent_id: str):
    db.revoke_agent(agent_id)
    return {"ok": True}


@app.post("/api/admin/agents/{agent_id}/tags", dependencies=[Depends(require_admin)])
def admin_agent_tags(agent_id: str, req: TagsRequest):
    db.set_agent_tags(agent_id, req.tags)
    return {"ok": True}


@app.post("/api/admin/jobs", dependencies=[Depends(require_admin)])
def admin_create_job(req: CreateJobRequest):
    target_count = len(req.target_agent_ids) if req.target_agent_ids else len(db.list_agents(10**9))
    if target_count > 1000:
        raise HTTPException(400, "A job can target up to 1,000 agents")
    if req.job_type == "file_install" and not db.get_approved_file(req.file_id):
        raise HTTPException(404, "No such approved file")
    job_id = str(uuid.uuid4())
    db.create_job(
        job_id, req.name, req.interpreter, req.target_agent_ids, req.timeout_seconds,
        job_type=req.job_type, script=req.script, file_id=req.file_id, install_args=req.install_args,
    )
    return {"job_id": job_id}


@app.get("/api/admin/jobs", dependencies=[Depends(require_admin)])
def admin_list_jobs():
    return {"jobs": db.list_jobs()}


@app.get("/api/admin/jobs/{job_id}", dependencies=[Depends(require_admin)])
def admin_get_job(job_id: str):
    job = db.get_job(job_id)
    if not job:
        raise HTTPException(404, "No such job")
    return job


@app.post("/api/admin/enrollment-keys", dependencies=[Depends(require_admin)])
def admin_create_enrollment_key(req: CreateEnrollmentKeyRequest):
    key = db.create_enrollment_key(req.label, req.expires_in_days, req.max_uses)
    return {"enrollment_key": key}


@app.get("/api/admin/enrollment-keys", dependencies=[Depends(require_admin)])
def admin_list_enrollment_keys():
    return {"keys": db.list_enrollment_keys()}


@app.delete("/api/admin/enrollment-keys/{key_hash}", dependencies=[Depends(require_admin)])
def admin_delete_enrollment_key(key_hash: str):
    if not db.delete_enrollment_key(key_hash):
        raise HTTPException(404, "No such enrollment key")
    return {"ok": True}


# ---------------- admin-facing endpoints: approved files (silent installs) ----------------

ALLOWED_FILE_EXTENSIONS = {
    ".exe", ".msi", ".msp",           # windows
    ".pkg", ".dmg",                    # macos
    ".deb", ".rpm", ".run", ".sh",     # linux
}


def _safe_filename(name: str) -> str:
    name = os.path.basename(name or "").strip()
    return name or "upload.bin"


@app.post("/api/admin/files", dependencies=[Depends(require_admin)])
async def admin_upload_file(
    request: Request,
    x_filename: str = Header(default=""),
    x_label: str = Header(default=""),
    x_platform: str = Header(default="any"),
    x_silent_args: str = Header(default=""),
):
    if x_platform not in ("windows", "macos", "linux", "any"):
        raise HTTPException(400, "platform must be one of windows, macos, linux, any")
    filename = _safe_filename(x_filename)
    ext = Path(filename).suffix.lower()
    if ext not in ALLOWED_FILE_EXTENSIONS:
        raise HTTPException(400, f"Unsupported file type {ext or '(none)'}. Allowed: {', '.join(sorted(ALLOWED_FILE_EXTENSIONS))}")

    APPROVED_FILES_DIR.mkdir(parents=True, exist_ok=True)
    file_id = str(uuid.uuid4())
    dest = APPROVED_FILES_DIR / f"{file_id}_{filename}"

    hasher = hashlib.sha256()
    size = 0
    with open(dest, "wb") as f:
        async for chunk in request.stream():
            size += len(chunk)
            if size > MAX_APPROVED_FILE_BYTES:
                f.close()
                dest.unlink(missing_ok=True)
                raise HTTPException(400, f"File exceeds the {MAX_APPROVED_FILE_BYTES // (1024*1024)} MiB limit")
            hasher.update(chunk)
            f.write(chunk)
    if size == 0:
        dest.unlink(missing_ok=True)
        raise HTTPException(400, "Uploaded file is empty")

    db.create_approved_file(
        file_id, x_label.strip(), filename, str(dest), x_platform,
        hasher.hexdigest(), size, x_silent_args.strip(),
    )
    return {"file": db.get_approved_file(file_id) | {"stored_path": None}}


@app.get("/api/admin/files", dependencies=[Depends(require_admin)])
def admin_list_files():
    return {"files": db.list_approved_files()}


@app.delete("/api/admin/files/{file_id}", dependencies=[Depends(require_admin)])
def admin_delete_file(file_id: str):
    f = db.get_approved_file(file_id)
    if not f:
        raise HTTPException(404, "No such file")
    if not db.delete_approved_file(file_id):
        raise HTTPException(404, "No such file")
    try:
        Path(f["stored_path"]).unlink(missing_ok=True)
    except OSError:
        pass
    return {"ok": True}


# ---------------- agent-facing endpoint: download an approved file ----------------

@app.get("/api/agent-files/{file_id}")
def agent_download_file(file_id: str, agent: dict = Depends(require_any_agent)):
    f = db.get_approved_file(file_id)
    if not f or not Path(f["stored_path"]).is_file():
        raise HTTPException(404, "No such file")
    return FileResponse(
        f["stored_path"],
        media_type="application/octet-stream",
        filename=f["original_filename"],
        headers={"X-SHA256": f["sha256"]},
    )


@app.get("/api/health")
def health():
    return {"ok": True, "version": "0.1.0"}


@app.get("/agent/install.sh", response_class=PlainTextResponse)
def agent_install_sh():
    return _read_install_script("install.sh")


@app.get("/agent/install-macos.sh", response_class=PlainTextResponse)
def agent_install_macos_sh():
    return _read_install_script("install-macos.sh")


@app.get("/agent/install.ps1", response_class=PlainTextResponse)
def agent_install_ps1():
    return _read_install_script("install.ps1")


def _read_install_script(name: str) -> str:
    path = INSTALL_SCRIPTS_DIR / name
    if not path.exists():
        raise HTTPException(404, f"Install script {name} not found on this server")
    return path.read_text()
