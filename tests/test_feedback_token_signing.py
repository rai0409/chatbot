"""PR F: feedback tokens are signed, expire and are bound to the tenant. Synthetic data only."""
from __future__ import annotations

import pytest
from fastapi import HTTPException

from rag_core import product_contract as pc

SECRET = "feedback-secret-not-a-real-one-0123456789"  # pragma: allowlist secret
T0 = 1_800_000_000.0


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    for name in ("FEEDBACK_TOKEN_SECRET", "FEEDBACK_TOKEN_TTL_SECONDS", "APP_ENV"):
        monkeypatch.delenv(name, raising=False)


def test_without_a_secret_tokens_stay_opaque_and_verify_is_not_enforced(monkeypatch):
    token = pc.generate_feedback_token()
    assert token and "." not in token
    assert pc.verify_feedback_token(token, "default") == "unsigned"


def test_a_signed_token_verifies_for_its_tenant_only(monkeypatch):
    monkeypatch.setenv("FEEDBACK_TOKEN_SECRET", SECRET)
    token = pc.generate_feedback_token("tenant_a", now=T0)
    assert pc.verify_feedback_token(token, "tenant_a", now=T0 + 10) == "ok"
    assert pc.verify_feedback_token(token, "tenant_b", now=T0 + 10) == "invalid"


def test_tampering_and_other_secrets_are_refused(monkeypatch):
    monkeypatch.setenv("FEEDBACK_TOKEN_SECRET", SECRET)
    token = pc.generate_feedback_token("tenant_a", now=T0)
    version, nonce, exp, sig = token.split(".")
    forged = ".".join([version, nonce, str(int(exp) + 99999), sig])
    assert pc.verify_feedback_token(forged, "tenant_a", now=T0 + 10) == "invalid"
    assert pc.verify_feedback_token(token[:-2] + "xx", "tenant_a", now=T0 + 10) == "invalid"
    assert pc.verify_feedback_token("opaque-token", "tenant_a", now=T0) == "invalid"
    monkeypatch.setenv("FEEDBACK_TOKEN_SECRET", SECRET + "-rotated")
    assert pc.verify_feedback_token(token, "tenant_a", now=T0 + 10) == "invalid"


def test_expiry(monkeypatch):
    monkeypatch.setenv("FEEDBACK_TOKEN_SECRET", SECRET)
    monkeypatch.setenv("FEEDBACK_TOKEN_TTL_SECONDS", "60")
    token = pc.generate_feedback_token("tenant_a", now=T0)
    assert pc.verify_feedback_token(token, "tenant_a", now=T0 + 59) == "ok"
    assert pc.verify_feedback_token(token, "tenant_a", now=T0 + 61) == "expired"


def test_the_envelope_carries_a_signed_token_bound_to_its_tenant(monkeypatch):
    monkeypatch.setenv("FEEDBACK_TOKEN_SECRET", SECRET)
    envelope = pc.build_product_answer_envelope(
        request_id="r1", trace_id="r1", tenant_id="tenant_a", answer_mode="fallback_no_answer",
        answer_text="", confidence_route="no_answer",
    )
    assert pc.verify_feedback_token(envelope["feedback_token"], "tenant_a") == "ok"


def _feedback(monkeypatch, token, tenant="tenant_a"):
    from webapi.routers import chat

    monkeypatch.setattr(chat, "append_feedback_audit_event", lambda event: True)
    req = chat.ProductFeedbackRequest(feedback_token=token, feedback_type="good", tenant_id=tenant)
    from webapi import api_auth

    for name in ("API_AUTH_ENABLED", "ENTERPRISE_OIDC_ENABLED", "ENTERPRISE_AUTH_ENABLED"):
        monkeypatch.delenv(name, raising=False)
    ctx = api_auth.require_api_auth_headers({})  # open development mode
    try:
        return chat.chat_feedback(req, api_auth=ctx)
    except HTTPException as exc:
        return exc


def test_the_route_refuses_unsigned_tokens_when_signing_is_on(monkeypatch):
    monkeypatch.setenv("FEEDBACK_TOKEN_SECRET", SECRET)
    result = _feedback(monkeypatch, "made-up-token")
    assert isinstance(result, HTTPException) and result.status_code == 400


def test_the_route_accepts_a_valid_token_and_refuses_another_tenants(monkeypatch):
    monkeypatch.setenv("FEEDBACK_TOKEN_SECRET", SECRET)
    token = pc.generate_feedback_token("tenant_a")
    assert _feedback(monkeypatch, token)["ok"] is True
    other = _feedback(monkeypatch, token, tenant="tenant_b")
    assert isinstance(other, HTTPException) and other.status_code == 400


def test_production_without_a_secret_is_a_startup_problem(monkeypatch):
    from webapi import production_guard

    monkeypatch.setenv("APP_ENV", "production")
    assert any("FEEDBACK_TOKEN_SECRET" in p for p in production_guard.production_problems())
    monkeypatch.setenv("FEEDBACK_TOKEN_SECRET", "short")
    assert any("FEEDBACK_TOKEN_SECRET" in p for p in production_guard.production_problems())
    monkeypatch.setenv("FEEDBACK_TOKEN_SECRET", SECRET)
    assert not any("FEEDBACK_TOKEN_SECRET" in p for p in production_guard.production_problems())
