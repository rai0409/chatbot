"""Backups from the console (installation administrator only): list the archives, run one now, see its status."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from fastapi.responses import JSONResponse

from webapi import admin_common, backups
from webapi.admin_auth import require_admin_auth, require_global

router = APIRouter()

_request_scope = admin_common.request_scope


class StartBackup(BaseModel):
    actor: str = Field("", max_length=64)


@router.get("/admin/backups")
def backups_list(request: Request = None, _admin_auth: None = Depends(require_admin_auth)):
    require_global(_request_scope(request))
    return {"archives": backups.archives(), "running": backups.running(), "available": backups.available(),
            "directory_note": "BACKUP_DIR", "restore_note": "scripts/restore.sh"}


@router.post("/admin/backups")
def backups_start(body: StartBackup, request: Request = None, _admin_auth: None = Depends(require_admin_auth)):
    scope = _request_scope(request)
    require_global(scope)
    who = admin_common.actor_identity(request, body.actor)["name"]
    try:
        job = backups.start(actor=who)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=501, detail=str(exc))
    except RuntimeError:
        raise HTTPException(status_code=409, detail="a backup is already running")
    admin_common.audit_admin(scope, "backup_started", job_id=job["job_id"], actor=who)
    return JSONResponse(status_code=202, content={"job_id": job["job_id"], "status": job["status"]})


@router.get("/admin/backups/jobs/{job_id}")
def backups_job(job_id: str, request: Request = None, _admin_auth: None = Depends(require_admin_auth)):
    require_global(_request_scope(request))
    job = backups.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")
    return job
