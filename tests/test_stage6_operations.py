from __future__ import annotations

# Stage 6 regression tests: production guard, input limits, exposure,
# security headers, audit chain, conversation store races, rate limit
# buckets, review actions, strict promotion gate, OIDC helpers.

import json

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

import config
from rag_core import audit_log
from rag_core.review_actions import latest_review_statuses
from webapi import conversation_store, main, oidc_hardening, production_guard
from webapi.api_auth import ApiAuthContext
from webapi.rate_limit import FixedWindowRateLimiter, enforce_rate_limit


# --- production guard ---------------------------------------------------------------


def test_production_guard_lists_unsafe_settings(monkeypatch):
    for name in ("API_AUTH_ENABLED", "ADMIN_AUTH_ENABLED", "ADMIN_AUTH_TOKEN", "ALLOWED_HOSTS", "METRICS_TOKEN", "SEARCH_DEBUG_ENABLED"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("APP_ENV", "production")
    with pytest.raises(RuntimeError) as exc:
        production_guard.check_production_settings()
    message = str(exc.value)
    assert "ADMIN_AUTH_TOKEN" in message and "ALLOWED_HOSTS" in message and "METRICS_TOKEN" in message


def test_production_guard_accepts_a_complete_configuration(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("API_AUTH_ENABLED", "true")
    monkeypatch.setenv("ADMIN_AUTH_ENABLED", "true")
    monkeypatch.setenv("ADMIN_AUTH_TOKEN", "a" * 40)
    monkeypatch.setenv("SEARCH_DEBUG_ENABLED", "false")
    monkeypatch.setenv("ALLOWED_HOSTS", "rag.example.co.jp")
    monkeypatch.setenv("METRICS_TOKEN", "m" * 40)
    for name in (
        "ADMIN_AUTH_ALLOW_INSECURE_DEV", "DISABLE_GUARD", "CORS_ALLOW_ORIGINS", "ENTERPRISE_OIDC_ENABLED",
        "API_AUTH_ALLOW_ANY_TENANT_WITHOUT_MAP", "OIDC_ALLOW_ANY_TENANT_WITHOUT_MAP", "OIDC_COOKIE_SECURE",
    ):
        monkeypatch.delenv(name, raising=False)
    production_guard.check_production_settings()
    assert production_guard.docs_enabled() is False


def test_development_mode_never_blocks(monkeypatch):
    monkeypatch.delenv("APP_ENV", raising=False)
    production_guard.check_production_settings()
    assert production_guard.docs_enabled() is True


# --- 6-1 input limits ---------------------------------------------------------------------


def test_question_length_and_top_k_are_bounded():
    with pytest.raises(ValidationError):
        main.ChatRequest(question="あ" * (main._MAX_QUESTION_CHARS + 1))
    with pytest.raises(ValidationError):
        main.ChatRequest(question="q", top_k=main._MAX_TOP_K + 1)
    with pytest.raises(ValidationError):
        main.SearchRequest(query="q", top_k=0)


def test_large_request_body_is_rejected(monkeypatch):
    monkeypatch.setenv("MAX_REQUEST_BYTES", "1000")
    client = TestClient(main.app)
    resp = client.post("/chat", content=b"{" + b" " * 2000 + b"}", headers={"content-type": "application/json"})
    assert resp.status_code == 413


# --- 6-2 / 6-10 exposure and headers -------------------------------------------------------


def test_health_is_minimal_in_production(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    assert main.health() == {"status": "ok"}
    monkeypatch.delenv("APP_ENV")
    assert "vectorstore_dir" in main.health()


def test_metrics_require_token_in_production(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("METRICS_TOKEN", "t" * 40)
    client = TestClient(main.app)
    assert client.get("/metrics").status_code == 401
    assert client.get("/metrics", headers={"Authorization": "Bearer wrong"}).status_code == 403
    assert client.get("/metrics", headers={"Authorization": "Bearer " + "t" * 40}).status_code == 200


def test_security_headers_are_set():
    resp = TestClient(main.app).get("/health")
    assert resp.headers["x-content-type-options"] == "nosniff"
    assert "frame-ancestors 'none'" in resp.headers["content-security-policy"]


# --- 6-4 audit log -----------------------------------------------------------------------------


def test_audit_records_actor_and_chain_detects_tampering(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "RUNS_DIR", str(tmp_path / "runs"))
    audit_log.set_audit_actor(ApiAuthContext(authenticated=True, key_fingerprint="fp123", role="user"))
    for i in range(3):
        audit_log.append_audit_event("chat", {"question": f"質問{i}"})
    path = tmp_path / "runs" / "audit" / "chat_events.jsonl"
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert rows[0]["actor"]["key_fingerprint"] == "fp123"
    assert audit_log.verify_chain(path)["ok"] is True

    rows[1]["question"] = "書き換え"
    path.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n", encoding="utf-8")
    assert audit_log.verify_chain(path)["ok"] is False


def test_audit_question_masking_and_rotation(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "RUNS_DIR", str(tmp_path / "runs"))
    monkeypatch.setenv("AUDIT_QUESTION_MODE", "mask")
    monkeypatch.setenv("AUDIT_MAX_BYTES", "300")
    for _ in range(4):
        audit_log.append_audit_event("chat", {"question": "03-1234-5678 か Taro_Yamada@example.co.jp に連絡"})
    audit_dir = tmp_path / "runs" / "audit"
    files = sorted(audit_dir.glob("chat_events*.jsonl"))
    assert len(files) >= 2
    blob = "".join(f.read_text(encoding="utf-8") for f in files)
    assert "1234" not in blob and "example.co.jp" not in blob
    assert audit_log.verify_chain(audit_dir / "chat_events.jsonl")["ok"] is True


# --- 6-5 / 6-7 / 6-8 ------------------------------------------------------------------------------


def test_conversation_delete_is_safe_when_already_removed(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "RUNS_DIR", str(tmp_path / "runs"))
    assert conversation_store.delete_thread("default", "fp", "missing-thread") is False


def test_anonymous_conversation_is_refused_in_production(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    from fastapi import HTTPException

    with pytest.raises(HTTPException):
        main._conversation_identity(ApiAuthContext())


def test_latest_review_action_wins(tmp_path):
    path = tmp_path / "review_actions.jsonl"
    events = [
        {"tenant_id": "t", "review_id": "r1", "status_after": "in_review", "created_at": "2026-01-01T00:00:00"},
        {"tenant_id": "t", "review_id": "r1", "status_after": "resolved", "created_at": "2026-01-02T00:00:00"},
        {"tenant_id": "u", "review_id": "r1", "status_after": "open", "created_at": "2026-01-03T00:00:00"},
    ]
    path.write_text("\n".join(json.dumps(e) for e in events) + "\n", encoding="utf-8")
    assert latest_review_statuses(path) == {("t", "r1"): "resolved", ("u", "r1"): "open"}


def test_anonymous_rate_limit_by_ip(monkeypatch):
    monkeypatch.setenv("RATE_LIMIT_ENABLED", "true")
    monkeypatch.setenv("RATE_LIMIT_REQUESTS_PER_MINUTE", "1")
    monkeypatch.setenv("RATE_LIMIT_ANONYMOUS_BY_IP", "true")
    limiter = FixedWindowRateLimiter(clock=lambda: 0.0)
    enforce_rate_limit(ApiAuthContext(), limiter=limiter, client_host="192.0.2.1")
    enforce_rate_limit(ApiAuthContext(), limiter=limiter, client_host="192.0.2.2")
    assert all(k.startswith("ip:") for k in limiter.snapshot())
    assert "192.0.2.1" not in json.dumps(limiter.snapshot())


# --- 6-12 strict promotion gate ------------------------------------------------------------------


def test_strict_gate_requires_cases_and_significance(tmp_path):
    from eval.rerank_promotion_gate import evaluate_rerank_promotion_gate

    profile = tmp_path / "profile.json"
    profile.write_text(
        json.dumps({"profile_name": "feedback_preview", "profile_type": "approved_similar_feedback_rerank", "production_enabled": False}),
        encoding="utf-8",
    )

    def _cmp(**metrics):
        path = tmp_path / "comparison.json"
        base = {
            "evaluated_cases": 60, "baseline_top1": 30, "preview_top1": 40, "top1_delta": 10,
            "baseline_top3": 40, "preview_top3": 45, "baseline_top5": 45, "preview_top5": 48,
            "improvement_count": 12, "regression_count": 0, "missing_expected_count": 0,
        }
        base.update(metrics)
        path.write_text(json.dumps({"metrics": base}), encoding="utf-8")
        return path

    ok = evaluate_rerank_promotion_gate(comparison_path=_cmp(), profile_path=profile, output=tmp_path / "o.json", strict=True)
    assert ok["safe_to_promote"] is True and ok["mcnemar_p"] < 0.05
    few = evaluate_rerank_promotion_gate(comparison_path=_cmp(evaluated_cases=10), profile_path=profile, output=tmp_path / "o.json", strict=True)
    assert few["safe_to_promote"] is False
    weak = evaluate_rerank_promotion_gate(
        comparison_path=_cmp(improvement_count=2, regression_count=0, top1_delta=2), profile_path=profile, output=tmp_path / "o.json", strict=True
    )
    assert weak["safe_to_promote"] is False


# --- 6-3 OIDC helpers --------------------------------------------------------------------------------


def test_discovery_requires_matching_issuer():
    meta = {
        "issuer": "https://idp.example.co.jp",
        "authorization_endpoint": "https://idp.example.co.jp/oauth2/authorize",
        "token_endpoint": "https://idp.example.co.jp/oauth2/token",
        "jwks_uri": "https://idp.example.co.jp/keys",
    }
    endpoints = oidc_hardening.discover("https://idp.example.co.jp", fetch=lambda url: meta, now=lambda: 0.0)
    assert endpoints["jwks_uri"] == "https://idp.example.co.jp/keys"
    with pytest.raises(ValueError):
        oidc_hardening.discover("https://other.example.co.jp", fetch=lambda url: meta, now=lambda: 0.0)


def test_jwks_cache_refetches_on_unknown_kid_with_rate_limit():
    calls = []
    clock = {"t": 0.0}

    def _fetch(uri):
        calls.append(uri)
        return {"keys": [{"kid": "k2" if len(calls) > 1 else "k1"}]}

    cache = oidc_hardening.JwksCache(fetch=_fetch, now=lambda: clock["t"])
    cache.get("u", "k1")
    cache.get("u", "k2")  # within the refresh interval: no refetch
    assert len(calls) == 1
    clock["t"] = 120.0
    assert cache.get("u", "k2")["keys"][0]["kid"] == "k2"
    assert len(calls) == 2


def test_azp_and_session_secret():
    oidc_hardening.validate_azp({"aud": "client"}, "client")
    with pytest.raises(ValueError):
        oidc_hardening.validate_azp({"aud": ["client", "other"]}, "client")
    with pytest.raises(ValueError):
        oidc_hardening.validate_azp({"aud": "client", "azp": "other"}, "client")
    with pytest.raises(ValueError):
        oidc_hardening.require_session_secret("short")
