"""PR B / U4, U12: header smuggling, error responses, request id, HSTS, slow bodies. Synthetic data only."""
from __future__ import annotations

import asyncio

import pytest

from webapi import security_middleware as sm


async def _echo_app(scope, receive, send):
    body = b""
    while True:
        message = await receive()
        body += message.get("body", b"")
        if not message.get("more_body"):
            break
    await send({"type": "http.response.start", "status": 200, "headers": [(b"content-type", b"text/plain")]})
    await send({"type": "http.response.body", "body": str(len(body)).encode()})


def _call(headers, bodies=(b"",), path="/x", app=_echo_app, receive_delay=0.0):
    scope = {"type": "http", "path": path, "method": "POST", "headers": headers}
    sent = []
    queue = list(bodies)

    async def receive():
        if receive_delay:
            await asyncio.sleep(receive_delay)
        if queue:
            chunk = queue.pop(0)
            return {"type": "http.request", "body": chunk, "more_body": bool(queue)}
        await asyncio.sleep(3600)

    async def send(message):
        sent.append(message)

    asyncio.run(sm.HardeningMiddleware(app)(scope, receive, send))
    start = next(m for m in sent if m["type"] == "http.response.start")
    return start["status"], {k.lower(): v for k, v in start["headers"]}


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    for name in ("MAX_REQUEST_BYTES", "APP_ENV", "SECURITY_HSTS", "REQUEST_BODY_IDLE_TIMEOUT_SECONDS"):
        monkeypatch.delenv(name, raising=False)


def test_conflicting_content_lengths_are_refused():
    status, _ = _call([(b"content-length", b"1"), (b"content-length", b"999999999")], [b"a"])
    assert status == 400


def test_identical_repeated_content_length_is_tolerated():
    status, _ = _call([(b"content-length", b"1"), (b"content-length", b"1")], [b"a"])
    assert status == 200


def test_content_length_with_chunked_encoding_is_refused():
    status, _ = _call([(b"content-length", b"1"), (b"transfer-encoding", b"chunked")], [b"a"])
    assert status == 400


@pytest.mark.parametrize("value", [b"-5", b"abc", b"1 2", b""])
def test_a_malformed_content_length_is_a_400_not_a_413(value):
    status, _ = _call([(b"content-length", value)], [b"a"])
    assert status == 400


def test_error_responses_carry_the_security_headers_and_a_request_id():
    status, headers = _call([(b"content-length", b"99999999")], [b"a"])
    assert status == 413
    assert headers[b"x-content-type-options"] == b"nosniff" and b"content-security-policy" in headers
    assert headers[b"x-request-id"]


def test_a_streamed_oversize_body_gets_the_same_headers(monkeypatch):
    monkeypatch.setenv("MAX_REQUEST_BYTES", "10")
    status, headers = _call([], [b"x" * 8, b"x" * 8])
    assert status == 413 and b"x-content-type-options" in headers


def test_a_valid_inbound_request_id_is_kept_and_a_bad_one_replaced():
    _, headers = _call([(b"x-request-id", b"abc-123_DEF")])
    assert headers[b"x-request-id"] == b"abc-123_DEF"
    _, headers = _call([(b"x-request-id", b"bad id\r\nX: y")])
    assert headers[b"x-request-id"] != b"bad id\r\nX: y" and len(headers[b"x-request-id"]) >= 16


def test_hsts_only_in_production_or_when_asked(monkeypatch):
    _, headers = _call([])
    assert b"strict-transport-security" not in headers
    monkeypatch.setenv("APP_ENV", "production")
    _, headers = _call([])
    assert headers[b"strict-transport-security"].startswith(b"max-age=")
    monkeypatch.delenv("APP_ENV")
    monkeypatch.setenv("SECURITY_HSTS", "true")
    _, headers = _call([])
    assert b"strict-transport-security" in headers


def test_a_stalled_body_times_out_with_408(monkeypatch):
    monkeypatch.setenv("REQUEST_BODY_IDLE_TIMEOUT_SECONDS", "0.2")

    async def slow_body_app(scope, receive, send):
        while True:
            message = await receive()
            if not message.get("more_body"):
                break

    # first chunk arrives, the rest never does
    scope = {"type": "http", "path": "/x", "method": "POST", "headers": []}
    sent = []
    chunks = [{"type": "http.request", "body": b"a", "more_body": True}]

    async def receive():
        if chunks:
            return chunks.pop(0)
        await asyncio.sleep(3600)

    async def send(message):
        sent.append(message)

    asyncio.run(sm.HardeningMiddleware(slow_body_app)(scope, receive, send))
    assert sent[0]["status"] == 408


def test_waiting_for_a_disconnect_after_the_body_is_not_timed_out(monkeypatch):
    # Streaming responses (SSE) listen on receive() long after the body is complete.
    monkeypatch.setenv("REQUEST_BODY_IDLE_TIMEOUT_SECONDS", "0.1")

    async def sse_like(scope, receive, send):
        await receive()  # the whole body
        task = asyncio.create_task(receive())  # disconnect listener
        await asyncio.sleep(0.4)
        task.cancel()
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    status, _ = _call([], [b"a"], app=sse_like)
    assert status == 200
