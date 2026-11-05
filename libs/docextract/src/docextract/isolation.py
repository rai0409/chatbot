"""Run a parsing function in its own process, with a time limit.

A hostile or broken file can make a parser loop, allocate without end or crash the interpreter. Called directly,
that takes the caller down with it. run_isolated() runs the function in a separate (spawned, never forked)
process:

    from docextract.isolation import run_isolated
    pages = run_isolated(extract_pages, "manual.pdf", timeout=120, memory_mb=4096)

- timeout: seconds; the process is killed when it is over (IsolatedFailure, error_type "Timeout")
- memory_mb: address-space limit of the process (0 = none; OCR engines need a lot, so set it per deployment)
- scrub_env: True (default): the process starts without environment variables that look like secrets
  (*_KEY, *_TOKEN, *_SECRET, *_PASSWORD, ...)

The function must be defined at module level (it is sent to the new process by name) and its argument and
result must be picklable. The ingestion job of the web API already converts every file this way
(rag_core/document_converters/isolated.py); this is for code that uses docextract on its own.
"""

from __future__ import annotations

import multiprocessing
import os
import re
from typing import Any, Callable

_CTX = multiprocessing.get_context("spawn")
_SECRET_RE = re.compile(r"(?:^|_)(?:KEY|TOKEN|SECRET|PASSWORD|PASSWD|CREDENTIALS?)(?:$|_)", re.IGNORECASE)
_KEEP_PREFIXES = ("DOCEXTRACT_",)


class IsolatedFailure(Exception):
    """The function failed in the child process. error_type: its exception name, "Timeout" or "Crashed"."""

    def __init__(self, error_type: str, message: str = "") -> None:
        super().__init__(message or error_type)
        self.error_type = error_type


def _child(conn, func: Callable[[Any], Any], arg: Any, memory_bytes: int, scrub_env: bool) -> None:
    try:
        if scrub_env:
            for name in [n for n in os.environ if not n.upper().startswith(_KEEP_PREFIXES) and _SECRET_RE.search(n)]:
                os.environ.pop(name, None)
        if memory_bytes > 0:
            try:
                import resource

                resource.setrlimit(resource.RLIMIT_AS, (memory_bytes, memory_bytes))
            except (ImportError, ValueError, OSError):
                pass
        conn.send(("ok", func(arg)))
    except BaseException as exc:  # noqa: BLE001 - reported to the parent
        try:
            conn.send(("error", type(exc).__name__, str(exc)[:300]))
        except Exception:  # noqa: BLE001 - the result itself could not be sent
            pass
    finally:
        conn.close()


def run_isolated(func: Callable[[Any], Any], arg: Any = None, *, timeout: float = 120.0, memory_mb: float = 0,
                 scrub_env: bool = True) -> Any:
    parent, child = _CTX.Pipe(duplex=False)
    process = _CTX.Process(target=_child, args=(child, func, arg, int(memory_mb * 1024 * 1024), scrub_env), daemon=True)
    process.start()
    child.close()
    try:
        if not parent.poll(timeout):
            raise IsolatedFailure("Timeout", f"over {timeout:g} seconds")
        try:
            message = parent.recv()
        except (EOFError, OSError):
            raise IsolatedFailure("Crashed", "the process stopped without a result") from None
    finally:
        if process.is_alive():
            process.kill()
        process.join(timeout=5)
        parent.close()
    if message[0] == "ok":
        return message[1]
    raise IsolatedFailure(message[1], message[2])
