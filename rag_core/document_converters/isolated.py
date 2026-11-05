"""Document conversion in worker processes with a time limit (stage 14).

One hostile or pathological file (OCR that never ends, a parser loop, memory
exhaustion) must not stall the ingestion queue or the API process. Each file
is converted in a separate worker process; a worker that passes the time
limit is killed and the file fails with ConversionTimeout, a worker that dies
fails it with ConversionCrashed. Workers are reused between files and jobs
(starting a process costs about a second) and replaced after
_TASKS_PER_WORKER files so memory does not accumulate.

  INGEST_CONVERT_TIMEOUT_SECONDS  per file (default 600; 0 = convert in the
                                  calling process without a limit, as before)
  INGEST_CONVERT_WORKERS          parallel workers (default 1)
  INGEST_CONVERT_MAX_MEMORY_MB    address-space limit of a worker (default 0 = none: OCR engines map a lot of
                                  memory, so set it per deployment, e.g. 4096); a conversion that exceeds it
                                  fails with MemoryError instead of taking the host down
  INGEST_CONVERT_SCRUB_ENV        true (default): a worker starts without the secret-looking environment
                                  variables of the API process (*_KEY, *_TOKEN, *_SECRET, *_PASSWORD, ...), so a
                                  parser exploit finds no credentials to read
"""
from __future__ import annotations

import atexit
import multiprocessing
import os
import re
import threading
import time
from multiprocessing.connection import wait
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

_TASKS_PER_WORKER = 200
_CTX = multiprocessing.get_context("spawn")  # the API process has threads: never fork it
_LOCK = threading.Lock()
_IDLE: List["_Worker"] = []


class ConversionFailed(Exception):
    """A conversion that failed in a worker; error_type names the original exception."""

    def __init__(self, error_type: str, message: str = "") -> None:
        super().__init__(message or error_type)
        self.error_type = error_type


def timeout_seconds() -> float:
    try:
        return float(str(os.getenv("INGEST_CONVERT_TIMEOUT_SECONDS", "") or "").strip() or 600)
    except ValueError:
        return 600.0


_SECRET_NAME_RE = re.compile(r"(?:^|_)(?:KEY|TOKEN|SECRET|PASSWORD|PASSWD|CREDENTIALS?)(?:$|_)", re.IGNORECASE)
_KEEP_PREFIXES = ("INGEST_", "DOCEXTRACT_")


def secret_variable_names(environ) -> List[str]:
    return [name for name in environ if not name.upper().startswith(_KEEP_PREFIXES) and _SECRET_NAME_RE.search(name)]


def scrub_enabled() -> bool:
    return str(os.getenv("INGEST_CONVERT_SCRUB_ENV", "") or "true").strip().lower() not in {"0", "false", "no", "off"}


def memory_limit_bytes() -> int:
    try:
        return max(0, int(float(str(os.getenv("INGEST_CONVERT_MAX_MEMORY_MB", "") or "0").strip()) * 1024 * 1024))
    except ValueError:
        return 0


def _harden_worker() -> None:
    """Runs first in a worker process: no inherited secrets, an optional memory limit."""
    if scrub_enabled():
        for name in secret_variable_names(os.environ):
            os.environ.pop(name, None)
    limit = memory_limit_bytes()
    if limit > 0:
        try:
            import resource

            resource.setrlimit(resource.RLIMIT_AS, (limit, limit))
        except (ImportError, ValueError, OSError):
            pass  # no resource module (Windows) or a limit the OS refuses: the time limit still applies


_WORKER_ORIGINAL: Dict[str, Optional[str]] = {}  # in the worker: the environment value before the first override


def _apply_environment(env: Optional[Dict[str, Optional[str]]]) -> None:
    """In the worker: set each override (name -> text); None gives back the worker's own value."""
    for name, text in (env or {}).items():
        _WORKER_ORIGINAL.setdefault(name, os.environ.get(name))
        if text is None:
            original = _WORKER_ORIGINAL[name]
            if original is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = original
        else:
            os.environ[name] = text


def _convert_one(path, tenant, source_doc, options):
    """One conversion with the per-job options: doc_type (PDF) and ocr (INGEST_PDF_OCR for this file only)."""
    from rag_core.document_converters import convert_file_to_canonical_chunks

    options = options or {}
    kwargs = {"doc_type": options["doc_type"]} if options.get("doc_type") else {}
    ocr = options.get("ocr")
    saved = os.environ.get("INGEST_PDF_OCR")
    if ocr is not None:
        os.environ["INGEST_PDF_OCR"] = "true" if ocr else "false"
    try:
        return convert_file_to_canonical_chunks(Path(path), tenant_id=tenant, source_doc=source_doc, **kwargs)
    finally:
        if ocr is not None:
            if saved is None:
                os.environ.pop("INGEST_PDF_OCR", None)
            else:
                os.environ["INGEST_PDF_OCR"] = saved


def _serve(conn) -> None:
    _harden_worker()

    while True:
        try:
            task = conn.recv()
        except EOFError:
            return
        if task is None:
            return
        if isinstance(task, dict):  # diagnostics: what does the worker's environment hold?
            _apply_environment(task.get("env"))
            conn.send(("ok", os.environ.get(task["read"]) if "read" in task else task.get("name") in os.environ))
            continue
        path, tenant, source_doc, options, env = task
        try:
            _apply_environment(env)
            rows = _convert_one(path, tenant, source_doc, options)
            conn.send(("ok", rows))
        except Exception as exc:  # noqa: BLE001 - reported to the parent
            conn.send(("error", type(exc).__name__, str(exc)[:200]))


class _Worker:
    def __init__(self) -> None:
        self.conn, child = _CTX.Pipe()
        self.process = _CTX.Process(target=_serve, args=(child,), name="kuraden-convert")
        self.process.start()
        child.close()
        self.tasks = 0

    def stop(self, *, kill: bool = False) -> None:
        if not kill:
            try:
                self.conn.send(None)
            except (OSError, ValueError):
                kill = True
        if kill and self.process.is_alive():
            self.process.kill()
        self.process.join(timeout=5)
        if self.process.is_alive():
            self.process.kill()
            self.process.join(timeout=5)
        self.conn.close()


def _take() -> _Worker:
    while _IDLE:
        worker = _IDLE.pop()
        if worker.process.is_alive():
            return worker
        worker.stop(kill=True)
    return _Worker()


def _give_back(worker: _Worker) -> None:
    if worker.tasks >= _TASKS_PER_WORKER or not worker.process.is_alive():
        worker.stop(kill=not worker.process.is_alive())
    else:
        _IDLE.append(worker)


def shutdown() -> None:
    """Stop the idle workers (application shutdown)."""
    with _LOCK:
        while _IDLE:
            _IDLE.pop().stop()


atexit.register(shutdown)


def _unpack(task) -> Tuple[Path, str, Optional[str], Dict[str, Any]]:
    """A task is (path, tenant, source_doc) or (path, tenant, source_doc, options)."""
    path, tenant, source_doc = task[0], task[1], task[2]
    return path, tenant, source_doc, (task[3] if len(task) > 3 else None) or {}


def convert_files(tasks: Sequence[Tuple], *, workers: int = 1, timeout: Optional[float] = None,
                  env: Optional[Dict[str, Optional[str]]] = None) -> Dict[Path, Any]:
    """(path, tenant, source_doc[, options]) -> chunk list, or the exception for that file.

    options: {"doc_type": ..., "ocr": ...} of this job (PDF only). env: the overrides of the console's ingestion
    settings for the worker processes (default: ingest_settings.worker_environment())."""
    if env is None:
        from rag_core import ingest_settings

        env = ingest_settings.worker_environment()
    timeout = timeout_seconds() if timeout is None else timeout
    out: Dict[Path, Any] = {}
    if timeout <= 0:
        for task in tasks:
            path, tenant, source_doc, options = _unpack(task)
            try:
                out[path] = _convert_one(path, tenant, source_doc, options)
            except Exception as exc:  # noqa: BLE001
                out[path] = exc
        return out
    pending = list(tasks)
    busy: Dict[Any, Tuple[_Worker, Path, float]] = {}
    # One job converts at a time; the workers are shared.
    with _LOCK:
        try:
            while pending or busy:
                while pending and len(busy) < max(1, workers):
                    path, tenant, source_doc, options = _unpack(pending.pop(0))
                    worker = _take()
                    worker.conn.send((str(path), tenant, source_doc, options, env))
                    worker.tasks += 1
                    busy[worker.conn] = (worker, path, time.monotonic() + timeout)
                now = time.monotonic()
                wait_for = max(0.0, min(deadline for _, _, deadline in busy.values()) - now)
                for conn in wait(list(busy), timeout=wait_for):
                    worker, path, _ = busy.pop(conn)
                    try:
                        message = conn.recv()
                    except (EOFError, OSError):
                        out[path] = ConversionFailed("ConversionCrashed", "the conversion worker stopped")
                        worker.stop(kill=True)
                        continue
                    if message[0] == "ok":
                        out[path] = message[1]
                    else:
                        out[path] = ConversionFailed(message[1], message[2])
                    _give_back(worker)
                now = time.monotonic()
                for conn, (worker, path, deadline) in list(busy.items()):
                    if now >= deadline:
                        busy.pop(conn)
                        out[path] = ConversionFailed("ConversionTimeout", f"over {timeout:g} seconds")
                        worker.stop(kill=True)
        finally:
            for worker, _, _ in busy.values():
                worker.stop(kill=True)
    return out


def worker_value(name: str, *, env: Optional[Dict[str, Optional[str]]] = None, timeout: float = 120.0) -> Optional[str]:
    """The value of an environment variable inside a conversion worker after `env` was applied (a test aid)."""
    with _LOCK:
        worker = _take()
        try:
            worker.conn.send({"env": env or {}, "read": name})
            if not worker.conn.poll(timeout):
                raise TimeoutError("the conversion worker did not answer")
            return worker.conn.recv()[1]
        finally:
            _give_back(worker)


def worker_sees_variable(name: str, *, timeout: float = 120.0) -> bool:
    """Whether an environment variable exists inside a conversion worker (a check of the scrubbing)."""
    with _LOCK:
        worker = _take()
        try:
            worker.conn.send({"name": name})
            if not worker.conn.poll(timeout):
                raise TimeoutError("the conversion worker did not answer")
            return bool(worker.conn.recv()[1])
        finally:
            _give_back(worker)
