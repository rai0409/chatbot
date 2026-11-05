"""Run the backup script (scripts/backup.sh) from the console and list the archives it made.

The administrator starts a backup; the server runs the fixed script with fixed arguments in the background (no
user-supplied text reaches the command line), one at a time, and keeps its output (bounded, secret-looking
values masked) for the status page. Archives hold everything needed to serve again, including records of what
people asked: there is no download here, the archive stays on the server (BACKUP_DIR, default <base>/backups) for
the operator to copy. Restoring is deliberately not offered: it replaces the live data and needs the service
stopped (scripts/restore.sh)."""
from __future__ import annotations

import os
import re
import shutil
import subprocess  # nosec B404 - a fixed script with fixed arguments
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

import config

MAX_OUTPUT_CHARS = 8000
MAX_JOBS_KEPT = 20
TIMEOUT_SECONDS = 6 * 3600
_SECRET_RE = re.compile(r"((?:KEY|TOKEN|SECRET|PASSWORD|PASSWD|CREDENTIAL)[A-Z0-9_]*\s*[=:]\s*)\S+", re.IGNORECASE)

_LOCK = threading.Lock()
_JOBS: Dict[str, Dict[str, Any]] = {}
_RUNNING: Dict[str, Optional[str]] = {"id": None}


def script_path() -> Path:
    return Path(config.BASE_DIR) / "scripts" / "backup.sh"


def backup_dir() -> Path:
    configured = str(os.getenv("BACKUP_DIR", "") or "").strip()
    path = Path(configured) if configured else Path(config.BASE_DIR) / "backups"
    return path if path.is_absolute() else Path(config.BASE_DIR) / path


def available() -> bool:
    return script_path().is_file() and shutil.which("bash") is not None


def _mask(text: str) -> str:
    return _SECRET_RE.sub(r"\1***", text)


def archives() -> List[Dict[str, Any]]:
    folder = backup_dir()
    rows: List[Dict[str, Any]] = []
    if folder.is_dir():
        for path in folder.glob("chatbot_backup_*.tar.gz"):
            try:
                stat = path.stat()
            except OSError:
                continue
            rows.append({"name": path.name, "bytes": stat.st_size, "modified": stat.st_mtime})
    return sorted(rows, key=lambda r: r["modified"], reverse=True)


def running() -> Optional[str]:
    return _RUNNING["id"]


def get(job_id: str) -> Optional[Dict[str, Any]]:
    job = _JOBS.get(job_id)
    return dict(job) if job else None


def start(*, actor: str) -> Dict[str, Any]:
    """Start a backup in the background. Raises RuntimeError("busy") when one runs, FileNotFoundError without the script."""
    if not available():
        raise FileNotFoundError("the backup script or bash is not available on this server")
    with _LOCK:
        if _RUNNING["id"] is not None:
            raise RuntimeError("busy")
        job_id = uuid.uuid4().hex[:12]
        job = {"job_id": job_id, "status": "running", "started_at": time.time(), "finished_at": None, "actor": actor, "output": "", "exit_code": None}
        _JOBS[job_id] = job
        for old in sorted(_JOBS, key=lambda k: _JOBS[k]["started_at"])[:-MAX_JOBS_KEPT]:
            _JOBS.pop(old, None)
        _RUNNING["id"] = job_id
    threading.Thread(target=_run, args=(job_id,), name="console-backup", daemon=True).start()
    return dict(job)


def _run(job_id: str) -> None:
    job = _JOBS[job_id]
    try:
        out = backup_dir()
        out.mkdir(parents=True, exist_ok=True)
        command = ["bash", str(script_path()), "--source-dir", str(config.BASE_DIR), "--output-dir", str(out)]
        done = subprocess.run(  # nosec B603 - fixed argv, no shell, no user input
            command, cwd=str(config.BASE_DIR), capture_output=True, text=True, timeout=TIMEOUT_SECONDS,
            env={k: v for k, v in os.environ.items() if k in ("PATH", "HOME", "LANG", "LC_ALL", "TMPDIR")},
        )
        text = _mask(((done.stdout or "") + (done.stderr or "")))
        job.update(exit_code=done.returncode, status="ok" if done.returncode == 0 else "error", output=text[-MAX_OUTPUT_CHARS:])
    except subprocess.TimeoutExpired:
        job.update(status="error", output="the backup took too long and was stopped")
    except Exception as exc:  # noqa: BLE001 - reported on the job
        job.update(status="error", output=f"{type(exc).__name__}")
    finally:
        job["finished_at"] = time.time()
        with _LOCK:
            _RUNNING["id"] = None
