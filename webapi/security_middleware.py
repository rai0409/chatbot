from __future__ import annotations

"""HTTP hardening middleware (6-1 request size, 6-10 security headers).

- Requests whose Content-Length exceeds MAX_REQUEST_BYTES (default 1 MiB)
  get 413 before the body is read. Chunked bodies without Content-Length are
  counted while streaming and cut at the same limit.
- Every response gets nosniff, a frame-ancestors policy, a referrer policy
  and (configurable) Content-Security-Policy. The default CSP restricts
  framing, plugins and base URLs but not scripts, so the bundled HTML pages
  with inline scripts keep working; tighten with SECURITY_CSP.
"""

import asyncio
import os
import re
import uuid
from typing import Iterable, Tuple

_DEFAULT_MAX_REQUEST_BYTES = 1024 * 1024
_DEFAULT_CSP = "frame-ancestors 'none'; base-uri 'self'; object-src 'none'; form-action 'self'"
_REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
_DEFAULT_BODY_IDLE_TIMEOUT = 30.0
_HSTS = b"max-age=31536000; includeSubDomains"


def max_request_bytes() -> int:
    try:
        value = int(str(os.getenv("MAX_REQUEST_BYTES", "")).strip() or _DEFAULT_MAX_REQUEST_BYTES)
    except ValueError:
        return _DEFAULT_MAX_REQUEST_BYTES
    return value if value > 0 else _DEFAULT_MAX_REQUEST_BYTES


_UPLOAD_PATHS = {"/admin/ingestion/upload"}
# Other routes whose body may be larger than the default request limit (a CSV of approved QA, as base64: 5 MiB of file).
_PATH_LIMITS = {"/admin/approved-qa/import": 8 * 1024 * 1024}


def max_upload_bytes() -> int:
    # Document uploads of the ingestion page (INGEST_UPLOAD_MAX_BYTES, default 50 MiB).
    try:
        value = int(str(os.getenv("INGEST_UPLOAD_MAX_BYTES", "")).strip() or 50 * 1024 * 1024)
    except ValueError:
        return 50 * 1024 * 1024
    return value if value > 0 else 50 * 1024 * 1024


def body_idle_timeout() -> float:
    """Seconds without a body chunk before the request is dropped with 408 (0 = never)."""
    try:
        value = float(str(os.getenv("REQUEST_BODY_IDLE_TIMEOUT_SECONDS", "")).strip() or _DEFAULT_BODY_IDLE_TIMEOUT)
    except ValueError:
        return _DEFAULT_BODY_IDLE_TIMEOUT
    return max(0.0, value)


def _hsts_enabled() -> bool:
    raw = str(os.getenv("SECURITY_HSTS", "")).strip().lower()
    if raw:
        return raw in {"1", "true", "yes", "on"}
    return str(os.getenv("APP_ENV", "")).strip().lower() == "production"


def security_headers() -> Iterable[Tuple[bytes, bytes]]:
    csp = str(os.getenv("SECURITY_CSP", "")).strip() or _DEFAULT_CSP
    headers = [
        (b"x-content-type-options", b"nosniff"),
        (b"x-frame-options", b"DENY"),
        (b"referrer-policy", b"no-referrer"),
        (b"content-security-policy", csp.encode("latin-1")),
        (b"permissions-policy", b"camera=(), microphone=(), geolocation=()"),
    ]
    if _hsts_enabled():
        headers.append((b"strict-transport-security", _HSTS))
    return tuple(headers)


class _TooLarge(Exception):
    pass


class _BodyTimeout(Exception):
    pass


def _request_id(headers) -> str:
    for key, value in headers:
        if key.lower() == b"x-request-id":
            text = value.decode("latin-1", "replace")
            if _REQUEST_ID_RE.fullmatch(text):
                return text
    return uuid.uuid4().hex


def _framing_problem(headers) -> str | None:
    """A request whose body length is ambiguous or malformed (the usual request-smuggling shapes)."""
    lengths = [value for key, value in headers if key.lower() == b"content-length"]
    chunked = any(key.lower() == b"transfer-encoding" for key, _ in headers)
    if lengths and chunked:
        return "both content-length and transfer-encoding"
    if any(not (v.isascii() and v.isdigit()) for v in lengths):
        return "invalid content-length"
    if len(set(lengths)) > 1:
        return "conflicting content-length headers"
    return None


class HardeningMiddleware:
    """Pure ASGI middleware (works with streaming responses)."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return
        raw_headers = list(scope.get("headers") or [])
        request_id = _request_id(raw_headers)
        scope.setdefault("state", {})["request_id"] = request_id
        extra = list(security_headers()) + [(b"x-request-id", request_id.encode("ascii"))]
        started = False

        async def send_with_headers(message):
            nonlocal started
            if message.get("type") == "http.response.start":
                started = True
                existing = {k.lower() for k, _ in message.get("headers") or []}
                message = dict(message)
                message["headers"] = list(message.get("headers") or []) + [
                    (k, v) for k, v in extra if k not in existing
                ]
            await send(message)

        problem = _framing_problem(raw_headers)
        if problem:
            await _send_error(send_with_headers, 400, "invalid request framing")
            return
        if str(scope.get("path") or "").startswith("/admin/ingestion"):
            try:  # a limit changed in the console (by another worker process) applies to this request
                from rag_core import ingest_settings

                ingest_settings.sync_environ()
            except Exception:  # noqa: BLE001 - the limits then stay as they were
                pass
        limit = max_upload_bytes() if scope.get("path") in _UPLOAD_PATHS else _PATH_LIMITS.get(scope.get("path"), 0) or max_request_bytes()
        declared = next((v for k, v in raw_headers if k.lower() == b"content-length"), None)
        if declared is not None and int(declared) > limit:
            await _send_error(send_with_headers, 413, "request body too large")
            return

        received = 0
        body_done = False  # set when the final body message (more_body false) has been received
        idle = body_idle_timeout()

        async def limited_receive():
            nonlocal received, body_done
            if idle > 0 and not body_done:
                # Only while the body is still arriving: later receive() calls
                # (waiting for a disconnect during a stream) may block for long.
                try:
                    message = await asyncio.wait_for(receive(), timeout=idle)
                except asyncio.TimeoutError:
                    raise _BodyTimeout() from None
            else:
                message = await receive()
            if message.get("type") == "http.request":
                received += len(message.get("body") or b"")
                if received > limit:
                    raise _TooLarge()
                if not message.get("more_body"):
                    body_done = True
            return message

        try:
            await self.app(scope, limited_receive, send_with_headers)
        except _TooLarge:
            if not started:
                await _send_error(send_with_headers, 413, "request body too large")
        except _BodyTimeout:
            if not started:
                await _send_error(send_with_headers, 408, "request body timed out")


async def _send_error(send, status: int, detail: str) -> None:
    body = ('{"detail":"%s"}' % detail).encode("utf-8")
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())],
        }
    )
    await send({"type": "http.response.body", "body": body})
