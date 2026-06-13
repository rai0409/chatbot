from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

import config
from rag_core import answer_cache
from webapi import main, metrics_registry, rate_limit
from webapi.api_auth import ApiAuthContext
from webapi.rate_limit import (
    FixedWindowRateLimiter,
    enforce_rate_limit,
    rate_limit_enabled,
    rate_limit_requests_per_minute,
    require_api_auth_rate_limited,
)


class FakeClock:
    def __init__(self, start: float = 0.0):
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def _clear_env(monkeypatch):
    monkeypatch.delenv("RATE_LIMIT_ENABLED", raising=False)
    monkeypatch.delenv("RATE_LIMIT_REQUESTS_PER_MINUTE", raising=False)
    monkeypatch.delenv("API_AUTH_ENABLED", raising=False)
    monkeypatch.delenv("API_AUTH_KEYS", raising=False)
    monkeypatch.delenv("API_AUTH_TENANT_MAP", raising=False)


# --- Knobs ----------------------------------------------------------------


def test_disabled_by_default(monkeypatch):
    _clear_env(monkeypatch)
    assert rate_limit_enabled() is False


def test_requests_per_minute_default_and_invalid_values(monkeypatch):
    _clear_env(monkeypatch)
    assert rate_limit_requests_per_minute() == 60

    for raw in ("0", "-5", "abc", ""):
        monkeypatch.setenv("RATE_LIMIT_REQUESTS_PER_MINUTE", raw)
        assert rate_limit_requests_per_minute() == 60

    monkeypatch.setenv("RATE_LIMIT_REQUESTS_PER_MINUTE", "3")
    assert rate_limit_requests_per_minute() == 3


# --- Limiter window math (clock-controlled, no sleeps) ---------------------


def test_allows_up_to_limit_then_rejects():
    clock = FakeClock()
    limiter = FixedWindowRateLimiter(clock=clock)

    for _ in range(3):
        allowed, retry_after = limiter.check("fp-a", limit=3)
        assert allowed is True
        assert retry_after == 0.0

    allowed, retry_after = limiter.check("fp-a", limit=3)
    assert allowed is False
    assert 0.0 < retry_after <= 60.0


def test_window_rollover_resets_budget():
    clock = FakeClock(start=10.0)
    limiter = FixedWindowRateLimiter(clock=clock)

    assert limiter.check("fp-a", limit=1)[0] is True
    assert limiter.check("fp-a", limit=1)[0] is False

    clock.advance(60.0)  # next window
    allowed, retry_after = limiter.check("fp-a", limit=1)
    assert allowed is True
    assert retry_after == 0.0


def test_retry_after_matches_time_to_window_end():
    clock = FakeClock(start=45.0)  # 15s left in the first 60s window
    limiter = FixedWindowRateLimiter(clock=clock)

    limiter.check("fp-a", limit=1)
    allowed, retry_after = limiter.check("fp-a", limit=1)
    assert allowed is False
    assert retry_after == pytest.approx(15.0)


def test_per_key_isolation():
    clock = FakeClock()
    limiter = FixedWindowRateLimiter(clock=clock)

    assert limiter.check("fp-a", limit=1)[0] is True
    assert limiter.check("fp-a", limit=1)[0] is False
    # A different fingerprint has its own untouched budget.
    assert limiter.check("fp-b", limit=1)[0] is True


def test_anonymous_requests_share_one_bucket(monkeypatch):
    _clear_env(monkeypatch)
    monkeypatch.setenv("RATE_LIMIT_ENABLED", "true")
    monkeypatch.setenv("RATE_LIMIT_REQUESTS_PER_MINUTE", "1")
    limiter = FixedWindowRateLimiter(clock=FakeClock())

    # No fingerprint (auth disabled) and non-context callers share "anonymous".
    enforce_rate_limit(ApiAuthContext(), limiter=limiter)
    with pytest.raises(HTTPException) as exc_info:
        enforce_rate_limit(None, limiter=limiter)
    assert exc_info.value.status_code == 429
    assert set(limiter.snapshot()) == {"anonymous"}


def test_enforce_is_noop_when_disabled(monkeypatch):
    _clear_env(monkeypatch)
    limiter = FixedWindowRateLimiter(clock=FakeClock())

    for _ in range(500):
        enforce_rate_limit(ApiAuthContext(key_fingerprint="fp-a"), limiter=limiter)
    # Disabled: nothing is ever counted, nothing raises.
    assert limiter.snapshot() == {}


def test_429_carries_retry_after_and_stable_detail(monkeypatch):
    _clear_env(monkeypatch)
    monkeypatch.setenv("RATE_LIMIT_ENABLED", "true")
    monkeypatch.setenv("RATE_LIMIT_REQUESTS_PER_MINUTE", "1")
    limiter = FixedWindowRateLimiter(clock=FakeClock(start=30.0))
    ctx = ApiAuthContext(key_fingerprint="fp-a")

    enforce_rate_limit(ctx, limiter=limiter)
    with pytest.raises(HTTPException) as exc_info:
        enforce_rate_limit(ctx, limiter=limiter)

    exc = exc_info.value
    assert exc.status_code == 429
    assert exc.detail == "rate limit exceeded"
    assert exc.headers["Retry-After"] == "30"
    assert int(exc.headers["Retry-After"]) >= 1


def test_limiter_state_holds_fingerprints_never_raw_keys(monkeypatch):
    _clear_env(monkeypatch)
    monkeypatch.setenv("RATE_LIMIT_ENABLED", "true")
    monkeypatch.setenv("RATE_LIMIT_REQUESTS_PER_MINUTE", "5")
    monkeypatch.setenv("API_AUTH_ENABLED", "true")
    monkeypatch.setenv("API_AUTH_KEYS", "raw-secret-key")
    limiter = FixedWindowRateLimiter(clock=FakeClock())

    from webapi.api_auth import require_api_auth_headers

    ctx = require_api_auth_headers({"X-Api-Key": "raw-secret-key"})
    enforce_rate_limit(ctx, limiter=limiter)

    state = json.dumps({k: list(v) for k, v in limiter.snapshot().items()})
    assert "raw-secret-key" not in state
    assert ctx.key_fingerprint in state


# --- Endpoint behavior ------------------------------------------------------


def _fake_answer(question="質問です"):
    ans = SimpleNamespace(
        intent="faq",
        guard_reason=None,
        used_fallback=False,
        citations=[],
        to_dict=lambda: {"answer_text": "回答です", "citations": [], "retrieved": []},
    )
    trace = {
        "request_id": "req-1",
        "normalized_query": question,
        "intent": "faq",
        "final_guard_reason": None,
        "final_used_fallback": False,
        "citations_count": 0,
        "latency_ms": 1,
        "after_rerank": [],
        "answer_mode": "grounded",
    }
    return ans, trace


def _setup_endpoint_env(monkeypatch, tmp_path, *, limit="2", clock=None):
    _clear_env(monkeypatch)
    monkeypatch.setenv("API_AUTH_ENABLED", "true")
    monkeypatch.setenv("API_AUTH_KEYS", "key-a,key-b")
    monkeypatch.setenv("RATE_LIMIT_ENABLED", "true")
    monkeypatch.setenv("RATE_LIMIT_REQUESTS_PER_MINUTE", limit)
    monkeypatch.setattr(
        rate_limit, "_limiter", FixedWindowRateLimiter(clock=clock or FakeClock())
    )

    monkeypatch.setattr(config, "APPROVED_QA_ENABLED", False)
    monkeypatch.setattr(config, "ANSWER_CACHE_ENABLED", False)
    monkeypatch.setattr(config, "RUNS_DIR", str(tmp_path / "runs"))
    answer_cache.clear()
    metrics_registry.reset()

    monkeypatch.setattr(main, "ensure_openai_client", lambda base_url=None: object())
    monkeypatch.setattr(main, "answer_query_with_trace", lambda *a, **k: _fake_answer())
    monkeypatch.setattr(main, "_approved_qa_lookup", lambda *a, **k: None)


def _post_chat(client, key="key-a"):
    return client.post(
        "/chat",
        json={"question": "質問です"},
        headers={"X-Api-Key": key},
    )


def test_chat_429_with_retry_after_when_over_limit(monkeypatch, tmp_path, caplog):
    _setup_endpoint_env(monkeypatch, tmp_path, limit="2")
    client = TestClient(main.app)

    assert _post_chat(client).status_code == 200
    assert _post_chat(client).status_code == 200
    with caplog.at_level("INFO"):
        resp = _post_chat(client)

    assert resp.status_code == 429
    assert resp.json() == {"detail": "rate limit exceeded"}
    retry_after = resp.headers.get("Retry-After")
    assert retry_after is not None and int(retry_after) >= 1
    # Raw keys never leak into the 429 body, headers, logs, or metrics.
    assert "key-a" not in resp.text
    assert "key-a" not in str(dict(resp.headers))
    assert "key-a" not in caplog.text
    assert "key-a" not in json.dumps(metrics_registry.snapshot())


def test_chat_window_rollover_recovers(monkeypatch, tmp_path):
    clock = FakeClock()
    _setup_endpoint_env(monkeypatch, tmp_path, limit="1", clock=clock)
    client = TestClient(main.app)

    assert _post_chat(client).status_code == 200
    assert _post_chat(client).status_code == 429
    clock.advance(60.0)
    assert _post_chat(client).status_code == 200


def test_endpoint_per_key_isolation(monkeypatch, tmp_path):
    _setup_endpoint_env(monkeypatch, tmp_path, limit="1")
    client = TestClient(main.app)

    assert _post_chat(client, key="key-a").status_code == 200
    assert _post_chat(client, key="key-a").status_code == 429
    # key-b is unaffected by key-a's exhausted budget.
    assert _post_chat(client, key="key-b").status_code == 200


def test_rate_limit_runs_after_auth(monkeypatch, tmp_path):
    _setup_endpoint_env(monkeypatch, tmp_path, limit="1")
    client = TestClient(main.app)

    assert _post_chat(client).status_code == 200
    # Exhausted budget for key-a, but a bad key still gets 403 (auth first)
    # and an absent key still gets 401 — never 429.
    assert _post_chat(client, key="wrong-key").status_code == 403
    resp = client.post("/chat", json={"question": "質問です"})
    assert resp.status_code == 401
    # And those rejected requests consumed no budget for anyone:
    assert _post_chat(client, key="key-b").status_code == 200


def test_chat_stream_429_before_streaming(monkeypatch, tmp_path):
    _setup_endpoint_env(monkeypatch, tmp_path, limit="1")
    monkeypatch.setattr(main, "answer_query_stream", lambda *a, **k: iter(()))
    client = TestClient(main.app)

    first = client.post(
        "/chat/stream", json={"question": "質問です"}, headers={"X-Api-Key": "key-a"}
    )
    assert first.status_code == 200

    resp = client.post(
        "/chat/stream", json={"question": "質問です"}, headers={"X-Api-Key": "key-a"}
    )
    assert resp.status_code == 429
    assert "text/event-stream" not in resp.headers.get("content-type", "")
    assert resp.headers.get("Retry-After") is not None


def test_default_off_leaves_endpoints_unlimited(monkeypatch, tmp_path):
    _setup_endpoint_env(monkeypatch, tmp_path)
    monkeypatch.delenv("RATE_LIMIT_ENABLED", raising=False)
    client = TestClient(main.app)

    for _ in range(10):
        assert _post_chat(client).status_code == 200


def test_health_and_metrics_never_limited(monkeypatch, tmp_path):
    _setup_endpoint_env(monkeypatch, tmp_path, limit="1")
    client = TestClient(main.app)

    for _ in range(5):
        assert client.get("/health").status_code == 200
        assert client.get("/metrics").status_code == 200


# --- Route coverage ---------------------------------------------------------


_LIMITED_PATHS = {"/chat", "/chat/stream", "/chat/product-preview", "/chat/feedback", "/search"}
_UNLIMITED_PATHS = {"/health", "/metrics"}


def _route_dependencies(route):
    deps = set()
    for param in route.dependant.dependencies:
        deps.add(param.call)
        for sub in param.dependencies:
            deps.add(sub.call)
    return deps


def test_route_coverage():
    routes = {route.path: route for route in main.app.routes if hasattr(route, "dependant")}

    for path in _LIMITED_PATHS:
        assert path in routes, path
        deps = _route_dependencies(routes[path])
        assert require_api_auth_rate_limited in deps, path

    for path in _UNLIMITED_PATHS:
        assert path in routes, path
        assert require_api_auth_rate_limited not in _route_dependencies(routes[path]), path
