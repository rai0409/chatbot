"""S1: runtime settings, the integration registry with its three layers, the egress checks, the status API,
and the deployment-specific values that left the code."""
import json
import threading

import pytest
from fastapi.testclient import TestClient

import config
from rag_core import integrations, runtime_settings
from rag_core.connectors import outlook, sharepoint as sp

ADMIN_TOKEN = "installation-admin-token-not-a-secret-0123"
TENANT_A_TOKEN = "tenant-a-admin-token-not-a-secret-0123456"
ADMIN = {"x-admin-token": ADMIN_TOKEN}
TENANT_A = {"x-admin-token": TENANT_A_TOKEN}


@pytest.fixture
def store(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "RUNS_DIR", str(tmp_path / "runs"))
    monkeypatch.setattr(config, "RUNTIME_SETTINGS_DIR", "")
    monkeypatch.setattr(config, "EXTERNAL_INTEGRATIONS", [])
    return tmp_path


@pytest.fixture
def api(store, monkeypatch):
    for name in ("API_AUTH_ENABLED", "APP_ENV", "ENTERPRISE_OIDC_ENABLED", "ENTERPRISE_AUTH_ENABLED", "ADMIN_AUTH_ALLOW_INSECURE_DEV"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("ADMIN_AUTH_ENABLED", "true")
    monkeypatch.setenv("ADMIN_AUTH_TOKEN", ADMIN_TOKEN)
    monkeypatch.setenv("ADMIN_AUTH_TENANT_TOKENS", f"{TENANT_A_TOKEN}=tenant_a")
    monkeypatch.setattr(config, "INTEGRATION_APPROVALS_REQUIRED", 1)  # these tests are about the switches; approval is in test_approvals_s7.py
    from webapi import main

    return TestClient(main.app), store


# --- runtime settings -------------------------------------------------------------------------------------------------------------


def test_a_setting_needs_a_reason_and_an_author_and_keeps_both(store):
    with pytest.raises(runtime_settings.SettingsError):
        runtime_settings.set_value("integration.slack.enabled", True, by="山田太郎", reason=" ")
    with pytest.raises(runtime_settings.SettingsError):
        runtime_settings.set_value("integration.slack.enabled", True, by="", reason="r")
    record = runtime_settings.set_value("integration.slack.enabled", True, by="山田太郎", reason="alerts to the team channel")
    assert record["rev"] == 1 and runtime_settings.get("integration.slack.enabled") is True
    entry = runtime_settings.entry("integration.slack.enabled")
    assert entry["by"] == "山田太郎" and entry["reason"] == "alerts to the team channel" and entry["at"]
    assert runtime_settings.history()[0]["after"] is True and runtime_settings.history()[0]["before"] is None


def test_a_tenant_value_overrides_the_global_one_and_none_removes_it(store):
    runtime_settings.set_value("limit.demo", 5, by="a", reason="r")
    runtime_settings.set_value("limit.demo", 9, by="a", reason="r", tenant_id="tenant_a")
    assert runtime_settings.get("limit.demo", tenant_id="tenant_a") == 9
    assert runtime_settings.get("limit.demo", tenant_id="tenant_b") == 5 and runtime_settings.get("limit.demo") == 5
    runtime_settings.set_value("limit.demo", None, by="a", reason="back to the global value", tenant_id="tenant_a")
    assert runtime_settings.get("limit.demo", tenant_id="tenant_a") == 5
    assert runtime_settings.get("missing", "fallback") == "fallback"


def test_invalid_keys_tenants_and_values_are_refused(store):
    for key in ("", "Upper", "1abc", "a b", "x" * 70):
        with pytest.raises(runtime_settings.SettingsError):
            runtime_settings.set_value(key, 1, by="a", reason="r")
    with pytest.raises(runtime_settings.SettingsError):
        runtime_settings.set_value("k", 1, by="a", reason="r", tenant_id="../x")
    with pytest.raises(runtime_settings.SettingsError):
        runtime_settings.set_value("k", {"nested": 1}, by="a", reason="r")
    with pytest.raises(runtime_settings.SettingsError):
        runtime_settings.set_value("k", "x" * 2001, by="a", reason="r")


def test_an_unexpected_revision_is_a_conflict_and_nothing_is_written(store):
    runtime_settings.set_value("k", 1, by="a", reason="r")
    with pytest.raises(runtime_settings.Conflict):
        runtime_settings.set_value("k", 2, by="a", reason="r", expected_rev=0)
    assert runtime_settings.get("k") == 1 and runtime_settings.revision() == 1
    runtime_settings.set_value("k", 2, by="a", reason="r", expected_rev=1)
    assert runtime_settings.get("k") == 2


def test_a_damaged_file_means_every_switch_is_at_its_default(store):
    path = store / "runs" / "settings" / runtime_settings.FILE_NAME
    path.parent.mkdir(parents=True)
    path.write_text("{ not json", encoding="utf-8")
    assert runtime_settings.get("integration.slack.enabled", False) is False and runtime_settings.revision() == 0


def test_concurrent_changes_are_all_kept(store):
    def work(i):
        runtime_settings.set_value(f"k{i}", i, by="a", reason="r")

    threads = [threading.Thread(target=work, args=(i,)) for i in range(8)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert runtime_settings.revision() == 8 and all(runtime_settings.get(f"k{i}") == i for i in range(8))


def test_every_change_reaches_the_audit_log(store):
    runtime_settings.set_value("integration.slack.enabled", True, by="山田太郎", reason="start")
    lines = (store / "runs" / "audit" / "events.jsonl").read_text(encoding="utf-8").splitlines()
    event = json.loads(lines[-1])
    assert event["kind"] == "runtime_settings" and event["action"] == "setting_changed" and event["reason"] == "start"


# --- the registry and its three layers ----------------------------------------------------------------------------------------------


def test_everything_is_off_by_default(store):
    for name in integrations.GATED:
        current = integrations.status(name)
        assert current.enabled is False and current.layer == "environment" and current.reason == integrations.ENV_NOT_ALLOWED
        with pytest.raises(integrations.IntegrationDisabled):
            integrations.require(name)
        with pytest.raises(integrations.IntegrationDisabled):
            integrations.require_egress(name)


def test_all_three_layers_must_allow(store, monkeypatch):
    monkeypatch.setattr(config, "EXTERNAL_INTEGRATIONS", ["slack"])
    assert integrations.status("slack").reason == integrations.SWITCH_OFF  # layer 1 yes, layer 2 no
    integrations.set_switch("slack", True, by="a", reason="r")
    assert integrations.status("slack").enabled is True
    integrations.require("slack", "tenant_a")
    integrations.set_switch("slack", False, by="a", reason="r", tenant_id="tenant_a")
    assert integrations.status("slack", "tenant_a").reason == integrations.TENANT_OFF
    integrations.require("slack", "tenant_b")  # another tenant is not affected
    integrations.require_egress("slack")  # the egress check knows no tenant
    integrations.set_switch("slack", True, by="a", reason="r", tenant_id="tenant_a")  # lifts the restriction
    integrations.require("slack", "tenant_a")
    monkeypatch.setattr(config, "EXTERNAL_INTEGRATIONS", [])  # the environment layer wins over the console
    with pytest.raises(integrations.IntegrationDisabled) as caught:
        integrations.require_egress("slack")
    assert caught.value.status.layer == "environment"


def test_all_lists_every_name_of_the_environment_layer(store, monkeypatch):
    monkeypatch.setattr(config, "EXTERNAL_INTEGRATIONS", ["all"])
    assert all(integrations.env_allowed(name) for name in integrations.GATED)


def test_configured_but_off_is_reported_at_start_up(store, monkeypatch):
    monkeypatch.setenv("ALERT_SLACK_WEBHOOK", "https://hooks.example.co.jp/x")
    monkeypatch.setattr(config, "EXTERNAL_INTEGRATIONS", [])
    warnings = integrations.startup_warnings()
    assert any(w.startswith("slack: configured but off") for w in warnings)
    monkeypatch.delenv("ALERT_SLACK_WEBHOOK")
    assert not any(w.startswith("slack:") for w in integrations.startup_warnings())


def test_not_configured_is_a_state_of_its_own(store, monkeypatch):
    for name in ("ALERT_SMTP_HOST", "ALERT_EMAIL_TO", "ALERT_EMAIL_FROM"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(config, "EXTERNAL_INTEGRATIONS", ["smtp"])
    integrations.set_switch("smtp", True, by="a", reason="r")
    current = integrations.status("smtp")
    assert current.state == integrations.NOT_CONFIGURED and current.enabled is True and current.configured is False


def test_llm_and_oidc_are_reported_without_a_second_switch(store):
    assert integrations.status("llm").name == "llm"
    assert integrations.status("oidc").name == "oidc"
    with pytest.raises(KeyError):
        integrations.require("llm")
    with pytest.raises(runtime_settings.SettingsError):
        integrations.set_switch("llm", True, by="a", reason="r")


# --- the egress checks stop a request before anything is sent ----------------------------------------------------------------------------


def test_the_graph_transports_are_gated_separately(store, monkeypatch):
    sent = []
    monkeypatch.setattr(sp, "_send", lambda *a, **k: sent.append(a) or (200, b"{}", {}))
    monkeypatch.setattr(config, "EXTERNAL_INTEGRATIONS", ["sharepoint"])
    integrations.set_switch("sharepoint", True, by="a", reason="r")
    assert sp.urllib_transport("GET", "https://graph.example.co.jp/x", {})[0] == 200
    outlook_transport = sp.transport_for("outlook_draft")  # a different integration, still off
    with pytest.raises(integrations.IntegrationDisabled):
        outlook_transport("POST", "https://graph.example.co.jp/x", {}, b"{}")
    assert len(sent) == 1


def test_the_notification_transports_never_leave_while_off(store, monkeypatch):
    from webapi import notifications

    monkeypatch.setenv("ALERT_SLACK_WEBHOOK", "https://hooks.example.co.jp/x")
    monkeypatch.setenv("ALERT_SMTP_HOST", "smtp.example.co.jp")
    monkeypatch.setenv("ALERT_EMAIL_TO", "ops@example.co.jp")
    monkeypatch.setenv("ALERT_EMAIL_FROM", "bot@example.co.jp")
    opened = []
    monkeypatch.setattr(notifications.urllib.request, "urlopen", lambda *a, **k: opened.append(a))
    monkeypatch.setattr(notifications.smtplib, "SMTP", lambda *a, **k: opened.append(a))
    result = notifications.notify({"overall": "CRITICAL", "signals": []}, sleep=lambda s: None)
    assert opened == []
    assert result["results"]["slack"]["error_type"] == "IntegrationDisabled" and result["results"]["slack"]["attempts"] == 1
    assert result["results"]["email"]["reason"] == integrations.ENV_NOT_ALLOWED


# --- the status API --------------------------------------------------------------------------------------------------------------


def test_the_status_lists_every_integration_and_the_layer_that_said_no(api):
    client, _tmp = api
    body = client.get("/admin/system/status", headers=ADMIN).json()
    assert [i["name"] for i in body["integrations"]] == list(integrations.INTEGRATIONS)
    slack = next(i for i in body["integrations"] if i["name"] == "slack")
    assert slack["enabled"] is False and slack["layer"] == "environment" and body["environment"] == {"external_integrations": []}
    assert client.get("/admin/system/status").status_code in (401, 403)  # not without the administrator's credentials


def test_a_switch_needs_a_reason_and_is_audited(api, monkeypatch):
    client, tmp_path = api
    monkeypatch.setattr(config, "EXTERNAL_INTEGRATIONS", ["slack"])
    assert client.post("/admin/system/switches", headers=ADMIN, json={"name": "slack", "enabled": True, "reason": ""}).status_code == 422
    done = client.post("/admin/system/switches", headers=ADMIN, json={"name": "slack", "enabled": True, "reason": "team channel"})
    assert done.status_code == 200 and done.json()["change"]["reason"] == "team channel"
    assert next(i for i in done.json()["integrations"] if i["name"] == "slack")["enabled"] is True
    assert client.post("/admin/system/switches", headers=ADMIN, json={"name": "llm", "enabled": True, "reason": "x"}).status_code == 400
    actions = [json.loads(line)["action"] for line in (tmp_path / "runs" / "audit" / "events.jsonl").read_text(encoding="utf-8").splitlines()]
    assert "integration_switch" in actions and "setting_changed" in actions


def test_a_stale_revision_is_a_conflict(api, monkeypatch):
    client, _tmp = api
    monkeypatch.setattr(config, "EXTERNAL_INTEGRATIONS", ["slack"])
    client.post("/admin/system/switches", headers=ADMIN, json={"name": "slack", "enabled": True, "reason": "first"})
    stale = client.post("/admin/system/switches", headers=ADMIN,
                        json={"name": "slack", "enabled": False, "reason": "second", "expected_rev": 0})
    assert stale.status_code == 409


def test_a_tenant_administrator_can_only_restrict_their_own_tenant(api, monkeypatch):
    client, _tmp = api
    monkeypatch.setattr(config, "EXTERNAL_INTEGRATIONS", ["slack"])
    global_switch = client.post("/admin/system/switches", headers=TENANT_A, json={"name": "slack", "enabled": True, "reason": "x"})
    assert global_switch.status_code == 403  # the installation-wide switch is the installation administrator's
    other = client.post("/admin/system/switches", headers=TENANT_A,
                        json={"name": "slack", "enabled": False, "reason": "x", "tenant_id": "tenant_b"})
    assert other.status_code == 403
    own = client.post("/admin/system/switches", headers=TENANT_A,
                      json={"name": "slack", "enabled": False, "reason": "not for us", "tenant_id": "tenant_a"})
    assert own.status_code == 200
    changes = client.get("/admin/system/changes", headers=TENANT_A).json()["changes"]
    assert [c["tenant_id"] for c in changes] == ["tenant_a"]


# --- values that left the code ----------------------------------------------------------------------------------------------------------


def test_the_graph_endpoints_come_from_the_settings_and_default_to_the_former_values(monkeypatch):
    assert sp.graph_base() == sp.GRAPH and sp.token_url() == sp.TOKEN_URL and sp.graph_scope() == sp.SCOPE
    monkeypatch.setattr(config, "GRAPH_BASE_URL", "https://graph.example.co.jp/v1.0/")
    monkeypatch.setattr(config, "GRAPH_TOKEN_URL", "https://login.example.co.jp/{tenant}/token")
    monkeypatch.setattr(config, "GRAPH_SCOPE", "https://graph.example.co.jp/.default")
    assert sp.graph_base() == "https://graph.example.co.jp/v1.0" and sp.graph_scope().endswith("/.default")
    assert sp.token_url().format(tenant="t") == "https://login.example.co.jp/t/token"
    assert next(sp.GraphClient(lambda: "t", lambda *a: (200, b'{"value": []}', {})).delta_pages("d1")) is not None


def test_a_draft_uses_the_configured_graph_endpoint(monkeypatch):
    monkeypatch.setattr(config, "GRAPH_BASE_URL", "https://graph.example.co.jp/v1.0")
    calls = []

    def transport(method, url, headers, body=None):
        calls.append(url)
        return 201, b'{"id": "d1", "webLink": "https://mail.example.co.jp/d1"}', {}

    outlook.create_draft(sp.GraphClient(lambda: "t", transport), "legal@example.co.jp", to=["taro@example.co.jp"], subject="s", body="b")
    assert calls == ["https://graph.example.co.jp/v1.0/users/legal%40example.co.jp/messages"]


def test_timeouts_are_settings(monkeypatch):
    monkeypatch.setattr(config, "GRAPH_HTTP_TIMEOUT_SECONDS", 7.0)
    monkeypatch.setattr(config, "GRAPH_MAX_WAIT_SECONDS", 3.0)
    assert sp._http_timeout() == 7.0 and sp._max_wait() == 3.0
    waits = []
    answers = [(429, b"", {"retry-after": "99"}), (200, b"ok", {})]
    client = sp.GraphClient(lambda: "t", lambda *a: answers.pop(0), sleep=waits.append)
    assert client._request("https://graph.example.co.jp/x") == b"ok" and waits == [3.0]  # the wait is capped by the setting


def test_the_embedding_endpoint_can_differ_from_the_llm_endpoint(monkeypatch):
    from rag_core import embedding_provider

    seen = {}

    class Fake:
        def __init__(self, **kwargs):
            seen.update(kwargs)

    monkeypatch.setenv("OPENAI_API_KEY", "key-not-a-secret")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://llm.example.co.jp/v1")
    monkeypatch.setattr(embedding_provider, "_load_openai_class", lambda: Fake)
    monkeypatch.setattr(config, "EMBEDDING_BASE_URL", "")
    embedding_provider._create_openai_client()
    assert seen["base_url"] == "https://llm.example.co.jp/v1"
    monkeypatch.setattr(config, "EMBEDDING_BASE_URL", "http://localhost:8081/v1")
    embedding_provider._create_openai_client()
    assert seen["base_url"] == "http://localhost:8081/v1"
