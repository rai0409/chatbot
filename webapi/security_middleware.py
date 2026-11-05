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

import os
from typing import Iterable, Tuple

_DEFAULT_MAX_REQUEST_BYTES = 1024 * 1024
_DEFAULT_CSP = "frame-ancestors 'none'; base-uri 'self'; object-src 'none'; form-action 'self'"


def max_request_bytes() -> int:
    try:
        value = int(str(os.getenv("MAX_REQUEST_BYTES", "")).strip() or _DEFAULT_MAX_REQUEST_BYTES)
    except ValueError:
        return _DEFAULT_MAX_REQUEST_BYTES
    return value if value > 0 else _DEFAULT_MAX_REQUEST_BYTES


_UPLOAD_PATHS = {"/admin/ingestion/upload"}


def max_upload_bytes() -> int:
    # Document uploads of the ingestion page (INGEST_UPLOAD_MAX_BYTES, default 50 MiB).
    try:
        value = int(str(os.getenv("INGEST_UPLOAD_MAX_BYTES", "")).strip() or 50 * 1024 * 1024)
    except ValueError:
        return 50 * 1024 * 1024
    return value if value > 0 else 50 * 1024 * 1024


def security_headers() -> Iterable[Tuple[bytes, bytes]]:
    csp = str(os.getenv("SECURITY_CSP", "")).strip() or _DEFAULT_CSP
    return (
        (b"x-content-type-options", b"nosniff"),
        (b"x-frame-options", b"DENY"),
        (b"referrer-policy", b"no-referrer"),
        (b"content-security-policy", csp.encode("latin-1")),
        (b"permissions-policy", b"camera=(), microphone=(), geolocation=()"),
    )


class _TooLarge(Exception):
    pass


class HardeningMiddleware:
    """Pure ASGI middleware (works with streaming responses)."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return
        limit = max_upload_bytes() if scope.get("path") in _UPLOAD_PATHS else max_request_bytes()
        headers = dict(scope.get("headers") or [])
        declared = headers.get(b"content-length")
        if declared is not None:
            try:
                if int(declared) > limit:
                    await _send_413(send)
                    return
            except ValueError:
                await _send_413(send)
                return

        received = 0

        async def limited_receive():
            nonlocal received
            message = await receive()
            if message.get("type") == "http.request":
                received += len(message.get("body") or b"")
                if received > limit:
                    raise _TooLarge()
            return message

        extra = list(security_headers())

        async def send_with_headers(message):
            if message.get("type") == "http.response.start":
                existing = {k.lower() for k, _ in message.get("headers") or []}
                message = dict(message)
                message["headers"] = list(message.get("headers") or []) + [
                    (k, v) for k, v in extra if k not in existing
                ]
            await send(message)

        try:
            await self.app(scope, limited_receive, send_with_headers)
        except _TooLarge:
            await _send_413(send)


async def _send_413(send) -> None:
    body = b'{"detail":"request body too large"}'
    await send(
        {
            "type": "http.response.start",
            "status": 413,
            "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())],
        }
    )
    await send({"type": "http.response.body", "body": body})
