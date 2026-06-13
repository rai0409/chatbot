from __future__ import annotations

from types import SimpleNamespace

from fastapi.testclient import TestClient

import config
from eval.production_readiness_report import build_report
from rag_core import answer_cache
from webapi import main, metrics_registry, rate_limit
from webapi.rate_limit import FixedWindowRateLimiter


class FakeClock:
    def __init__(self, start: float = 0.0):
        self.now = start

    def __call__(self) -> float:
        return self.now


# --- Prometheus exposition format -----------------------------------------


def test_to_prometheus_renders_gauges_counters_and_labels():
    metrics_registry.reset()
    metrics_registry.increment("chat_answer_mode_total", "grounded")
    metrics_registry.increment("chat_answer_mode_total", "grounded")
    metrics_registry.increment("chat_used_fallback_total")

    payload = {
        "uptime_seconds": 5,
        "total_requests": 3,
        "error_requests": 1,
        "counters": metrics_registry.snapshot(),
    }
    text = metrics_registry.to_prometheus(payload)

    assert "# TYPE app_uptime_seconds gauge" in text
    assert "app_uptime_seconds 5" in text
    assert "# TYPE app_requests_total counter" in text
    assert "app_requests_total 3" in text
    assert "app_error_requests_total 1" in text
    # labeled counter
    assert 'chat_answer_mode_total{label="grounded"} 2' in text
    # 'total' bucket emits a bare metric (no label)
    assert "chat_used_fallback_total 1" in text
    # per-process caveat documented in the output
    assert "per-process" in text


def test_to_prometheus_escapes_label_values():
    payload = {
        "uptime_seconds": 0,
        "total_requests": 0,
        "error_requests": 0,
        "counters": {"weird_total": {'a"b\\c\nd': 1}},
    }
    text = metrics_registry.to_prometheus(payload)

    # quote, backslash, newline are escaped; no raw newline inside the line
    assert 'weird_total{label="a\\"b\\\\c\\nd"} 1' in text


def test_metrics_endpoint_prometheus_format_content_type_and_body():
    metrics_registry.reset()
    metrics_registry.increment("chat_answer_mode_total", "grounded")
    client = TestClient(main.app)

    resp = client.get("/metrics", params={"format": "prometheus"})

    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/plain")
    assert "version=0.0.4" in resp.headers["content-type"]
    body = resp.text
    assert "app_uptime_seconds" in body
    assert 'chat_answer_mode_total{label="grounded"} 1' in body


def test_metrics_endpoint_defaults_to_json():
    metrics_registry.reset()
    client = TestClient(main.app)

    resp = client.get("/metrics")

    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("application/json")
    payload = resp.json()
    assert set(payload.keys()) >= {"uptime_seconds", "total_requests", "error_requests", "counters"}


def test_prometheus_output_carries_no_raw_keys_or_query_text():
    # The registry only ever holds stable enum labels; even so, assert that a
    # realistic exposition has neither a configured raw key nor question text.
    metrics_registry.reset()
    metrics_registry.increment("api_auth_rejection_total", "invalid_credentials")
    metrics_registry.increment("api_rate_limited_total", "authenticated")
    payload = {
        "uptime_seconds": 1,
        "total_requests": 1,
        "error_requests": 0,
        "counters": metrics_registry.snapshot(),
    }
    text = metrics_registry.to_prometheus(payload)

    assert "secret-key" not in text
    assert "営業時間" not in text  # representative question text
    assert 'api_auth_rejection_total{label="invalid_credentials"} 1' in text
    assert 'api_rate_limited_total{label="authenticated"} 1' in text


# --- New counters increment on the rejection paths -------------------------


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


def _setup_auth_env(monkeypatch, tmp_path, *, rate_limit_enabled=False, limit="2", clock=None):
    monkeypatch.delenv("RATE_LIMIT_ENABLED", raising=False)
    monkeypatch.delenv("RATE_LIMIT_REQUESTS_PER_MINUTE", raising=False)
    monkeypatch.setenv("API_AUTH_ENABLED", "true")
    monkeypatch.setenv("API_AUTH_KEYS", "key-a,key-b")
    monkeypatch.setenv("API_AUTH_TENANT_MAP", "key-a=tenant_a,key-b=tenant_a|tenant_b")
    if rate_limit_enabled:
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


def test_auth_rejection_counter_missing_credentials(monkeypatch, tmp_path):
    _setup_auth_env(monkeypatch, tmp_path)
    client = TestClient(main.app)

    resp = client.post("/chat", json={"question": "質問です", "tenant_id": "tenant_a"})

    assert resp.status_code == 401
    snap = metrics_registry.snapshot()
    assert snap["api_auth_rejection_total"]["missing_credentials"] == 1
    # raw keys never appear in the counter state
    assert "key-a" not in str(snap)


def test_auth_rejection_counter_invalid_credentials(monkeypatch, tmp_path):
    _setup_auth_env(monkeypatch, tmp_path)
    client = TestClient(main.app)

    resp = client.post(
        "/chat",
        json={"question": "質問です", "tenant_id": "tenant_a"},
        headers={"X-Api-Key": "wrong-key"},
    )

    assert resp.status_code == 403
    snap = metrics_registry.snapshot()
    assert snap["api_auth_rejection_total"]["invalid_credentials"] == 1


def test_auth_rejection_counter_tenant_forbidden(monkeypatch, tmp_path):
    _setup_auth_env(monkeypatch, tmp_path)
    client = TestClient(main.app)

    # key-a is mapped to tenant_a only; requesting tenant_b is forbidden.
    resp = client.post(
        "/chat",
        json={"question": "質問です", "tenant_id": "tenant_b"},
        headers={"X-Api-Key": "key-a"},
    )

    assert resp.status_code == 403
    snap = metrics_registry.snapshot()
    assert snap["api_auth_rejection_total"]["tenant_forbidden"] == 1


def test_rate_limited_counter_increments_on_429(monkeypatch, tmp_path):
    _setup_auth_env(monkeypatch, tmp_path, rate_limit_enabled=True, limit="1")
    client = TestClient(main.app)

    ok = client.post(
        "/chat", json={"question": "質問です", "tenant_id": "tenant_a"}, headers={"X-Api-Key": "key-a"}
    )
    assert ok.status_code == 200
    limited = client.post(
        "/chat", json={"question": "質問です", "tenant_id": "tenant_a"}, headers={"X-Api-Key": "key-a"}
    )
    assert limited.status_code == 429

    snap = metrics_registry.snapshot()
    assert snap["api_rate_limited_total"]["authenticated"] == 1
    # the successful auth path created no auth-rejection counter
    assert "api_auth_rejection_total" not in snap
    assert "key-a" not in str(snap)


def test_successful_request_records_no_rejection_counters(monkeypatch, tmp_path):
    _setup_auth_env(monkeypatch, tmp_path)
    client = TestClient(main.app)

    resp = client.post(
        "/chat",
        json={"question": "質問です", "tenant_id": "tenant_a"},
        headers={"X-Api-Key": "key-a"},
    )

    assert resp.status_code == 200
    snap = metrics_registry.snapshot()
    assert "api_auth_rejection_total" not in snap
    assert "api_rate_limited_total" not in snap


# --- Readiness report observes the security-ops items ----------------------


def test_readiness_report_observes_security_ops_items():
    report = build_report()
    checks = report["safety_checks"]

    for key in (
        "api_auth_guard_present",
        "api_key_tenant_authorization_present",
        "rate_limit_guard_present",
        "security_operations_doc_present",
    ):
        assert checks[key] is True, key

    sec = report["security_operations"]
    assert sec["api_auth_helper_present"] is True
    assert sec["rate_limit_helper_present"] is True
    assert sec["rate_limit_default_off"] is True
    assert sec["protected_post_routes_detected"] >= 5
    assert sec["security_operations_doc_present"] is True
    # security-ops items are not blocking (all present)
    for key in (
        "api_auth_guard_present",
        "api_key_tenant_authorization_present",
        "rate_limit_guard_present",
        "security_operations_doc_present",
    ):
        assert key not in report["readiness_decision"]["blockers"]


def test_readiness_markdown_includes_security_operations_section():
    from eval.production_readiness_report import render_markdown

    md = render_markdown(build_report())
    assert "## Security Operations" in md
    assert "Rate limiter present" in md
