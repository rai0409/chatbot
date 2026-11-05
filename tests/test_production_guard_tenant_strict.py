import pytest

from webapi import production_guard


def _complete_production_env(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("API_AUTH_ENABLED", "true")
    monkeypatch.setenv("ADMIN_AUTH_ENABLED", "true")
    monkeypatch.setenv("ADMIN_AUTH_TOKEN", "a" * 40)
    monkeypatch.setenv("SEARCH_DEBUG_ENABLED", "false")
    monkeypatch.setenv("ALLOWED_HOSTS", "rag.example.co.jp")
    monkeypatch.setenv("METRICS_TOKEN", "m" * 40)
    monkeypatch.setenv("EMBED_FINGERPRINT_STRICT", "true")
    for name in (
        "ADMIN_AUTH_ALLOW_INSECURE_DEV", "DISABLE_GUARD", "CORS_ALLOW_ORIGINS", "ENTERPRISE_OIDC_ENABLED",
        "API_AUTH_ALLOW_ANY_TENANT_WITHOUT_MAP", "OIDC_ALLOW_ANY_TENANT_WITHOUT_MAP", "OIDC_COOKIE_SECURE",
        "WEB_CONCURRENCY", "VECTOR_BACKEND",
    ):
        monkeypatch.delenv(name, raising=False)


def test_production_requires_the_strict_tenant_filter(monkeypatch):
    _complete_production_env(monkeypatch)
    monkeypatch.delenv("TENANT_FILTER_STRICT", raising=False)
    assert any("TENANT_FILTER_STRICT" in p for p in production_guard.production_problems())
    with pytest.raises(RuntimeError, match="TENANT_FILTER_STRICT"):
        production_guard.check_production_settings()
    monkeypatch.setenv("TENANT_FILTER_STRICT", "false")
    assert any("TENANT_FILTER_STRICT" in p for p in production_guard.production_problems())
    monkeypatch.setenv("TENANT_FILTER_STRICT", "true")
    assert production_guard.production_problems() == []
    production_guard.check_production_settings()


def test_development_mode_does_not_require_the_strict_tenant_filter(monkeypatch):
    monkeypatch.delenv("APP_ENV", raising=False)
    monkeypatch.delenv("TENANT_FILTER_STRICT", raising=False)
    production_guard.check_production_settings()
