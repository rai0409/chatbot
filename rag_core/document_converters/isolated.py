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
"""
from __future__ import annotations

import atexit
import multiprocessing
import os
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


def _serve(conn) -> None:
    from rag_core.document_converters import convert_file_to_canonical_chunks

    while True:
        try:
            task = conn.recv()
        except EOFError:
            return
        if task is None:
            return
        path, tenant, source_doc = task
        try:
            rows = convert_file_to_canonical_chunks(Path(path), tenant_id=tenant, source_doc=source_doc)
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


def convert_files(tasks: Sequence[Tuple[Path, str, Optional[str]]], *, workers: int = 1,
                  timeout: Optional[float] = None) -> Dict[Path, Any]:
    """(path, tenant, source_doc) -> chunk list, or the exception for that file."""
    timeout = timeout_seconds() if timeout is None else timeout
    out: Dict[Path, Any] = {}
    if timeout <= 0:
        from rag_core.document_converters import convert_file_to_canonical_chunks

        for path, tenant, source_doc in tasks:
            try:
                out[path] = convert_file_to_canonical_chunks(path, tenant_id=tenant, source_doc=source_doc)
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
                    path, tenant, source_doc = pending.pop(0)
                    worker = _take()
                    worker.conn.send((str(path), tenant, source_doc))
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
