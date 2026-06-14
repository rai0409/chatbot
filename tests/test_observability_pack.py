from __future__ import annotations

import json
from pathlib import Path

import yaml

from webapi.alerting import DEFAULT_THRESHOLDS

PACK = Path(__file__).resolve().parents[1] / "deploy" / "observability"

# Counter / gauge names the app actually exposes (safe, enum-labelled).
_KNOWN_METRICS = {
    "app_uptime_seconds", "app_requests_total", "app_error_requests_total",
    "chat_answer_mode_total", "chat_guard_reason_total", "chat_used_fallback_total",
    "chat_provider_error_total", "chat_cache_hit_total", "chat_tenant_profile_total",
    "chat_feedback_total", "api_rate_limited_total", "api_auth_rejection_total",
    "api_enterprise_auth_total", "api_oidc_auth_total", "api_role_total", "up",
}
_FORBIDDEN = ("sk-", "Bearer ", "X-Api-Key", "OPENAI_API_KEY", "ADMIN_AUTH_TOKEN",
              "OIDC_CLIENT_SECRET", "OIDC_SESSION_SECRET", "ENTERPRISE_AUTH_TRUST_TOKEN",
              "password", "tenant_a", "tenant_b")


def _read(name):
    return (PACK / name).read_text(encoding="utf-8")


def test_files_exist():
    for f in ("prometheus.yml", "alert_rules.yml", "grafana_dashboard.json"):
        assert (PACK / f).is_file(), f


def test_prometheus_yaml_valid_and_scrapes_metrics():
    cfg = yaml.safe_load(_read("prometheus.yml"))
    assert "scrape_configs" in cfg
    job = cfg["scrape_configs"][0]
    assert job["metrics_path"] == "/metrics"
    assert job["params"]["format"] == ["prometheus"]


def test_alert_rules_yaml_valid_and_thresholds_match_alerting():
    rules_doc = yaml.safe_load(_read("alert_rules.yml"))
    exprs = []
    for group in rules_doc["groups"]:
        for rule in group["rules"]:
            assert "alert" in rule and "expr" in rule
            exprs.append(rule["expr"])
    blob = " ".join(exprs)
    # critical thresholds mirror webapi/alerting.py defaults
    assert str(DEFAULT_THRESHOLDS["error_rate"]["critical"]) in blob       # 0.1
    assert str(DEFAULT_THRESHOLDS["fallback_rate"]["critical"]) in blob    # 0.6
    assert str(DEFAULT_THRESHOLDS["guard_trip_rate"]["warn"]) in blob      # 0.4
    assert str(DEFAULT_THRESHOLDS["rate_limited"]["critical"]) in blob     # 25
    assert str(DEFAULT_THRESHOLDS["auth_rejection"]["critical"]) in blob   # 50


def test_grafana_dashboard_json_valid():
    dash = json.loads(_read("grafana_dashboard.json"))
    assert dash["uid"] == "kuraden-ops"
    assert len(dash["panels"]) >= 6


def _metric_tokens(text):
    import re
    # crude metric-name extraction: identifiers that look like metric names
    return set(re.findall(r"[a-zA-Z_][a-zA-Z0-9_]*_total|app_[a-z_]+|up", text))


def test_only_known_metric_names_referenced():
    for name in ("alert_rules.yml", "grafana_dashboard.json"):
        for token in _metric_tokens(_read(name)):
            assert token in _KNOWN_METRICS, f"{name}: unknown metric {token}"


def test_pack_contains_no_secrets_or_tenant_data():
    for name in ("prometheus.yml", "alert_rules.yml", "grafana_dashboard.json"):
        blob = _read(name)
        for forbidden in _FORBIDDEN:
            assert forbidden not in blob, f"{name} contains forbidden token {forbidden}"
