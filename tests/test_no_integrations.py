"""S1: the product works without any external integration, and nothing leaves while they are off.

Every socket connection fails the test, so a request that would leave the machine cannot pass unnoticed."""
import socket

import pytest

import config
from rag_core import change_ledger, contract_notify, integrations, source_registry as registry, source_sync
from webapi import notifications


@pytest.fixture
def closed(monkeypatch, tmp_path):
    def refuse(*args, **kwargs):
        raise AssertionError("a network connection was attempted while every integration is off")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)
    roots = tmp_path / "ingest"
    roots.mkdir()
    monkeypatch.setenv("INGEST_ALLOWED_ROOTS", str(roots))
    monkeypatch.setenv("SOURCES_DIR", str(roots / "state"))
    monkeypatch.setenv("CONTRACTS_DIR", str(tmp_path / "contracts"))
    monkeypatch.setattr(config, "BASE_DIR", tmp_path)
    monkeypatch.setattr(config, "RUNS_DIR", str(tmp_path / "runs"))
    monkeypatch.setattr(config, "RUNTIME_SETTINGS_DIR", "")
    monkeypatch.setattr(config, "EXTERNAL_INTEGRATIONS", [])
    monkeypatch.setattr(config, "LLM_ENABLED", False)
    # Settings for every integration are present: they must not be enough.
    monkeypatch.setenv("ALERT_SLACK_WEBHOOK", "https://hooks.example.co.jp/x")
    monkeypatch.setenv("ALERT_SMTP_HOST", "smtp.example.co.jp")
    monkeypatch.setenv("ALERT_EMAIL_TO", "ops@example.co.jp")
    monkeypatch.setenv("ALERT_EMAIL_FROM", "bot@example.co.jp")
    monkeypatch.setenv("SHAREPOINT_ACCESS_TOKEN", "token-not-a-secret")
    monkeypatch.setenv("OUTLOOK_ACCESS_TOKEN", "token-not-a-secret")
    return roots


def test_a_folder_source_is_scanned_without_any_integration(closed):
    (closed / "docs").mkdir()
    (closed / "docs" / "rules.pdf").write_bytes(b"%PDF-1.4 sample")
    registry.create("local1", tenant_id="tenant_a", kind="folder", path=str(closed / "docs"), created_by="山田太郎")
    out = source_sync.scan_source("local1")
    assert out["counts"] == {"added": 1, "modified": 0, "deleted": 0}


def test_a_remote_source_is_recorded_as_off_and_never_as_deleted(closed):
    registry.create("remote1", tenant_id="tenant_a", kind="sharepoint", options={"drive_id": "d", "folder": "/Docs"},
                    created_by="山田太郎")
    out = source_sync.scan_source("remote1")
    assert out["error"] == "integration_disabled"
    assert change_ledger.read_entries("remote1")[0]["kind"] == "scan_failed"


def test_alerts_and_contract_reminders_do_not_leave(closed):
    result = notifications.notify({"overall": "CRITICAL", "signals": []}, sleep=lambda s: None)
    assert {k: v["ok"] for k, v in result["results"].items()} == {"slack": False, "email": False}
    sent = contract_notify.default_send("subject", "body")
    assert sent == {"ok": False, "reason": "integration_off:env_not_allowed"}
    assert contract_notify.run("tenant_a", dry_run=True)["due"] == 0


def test_every_integration_reports_why_it_is_off(closed):
    states = {s.name: s for s in integrations.all_status("tenant_a")}
    for name in integrations.GATED:
        assert states[name].enabled is False and states[name].configured is True and states[name].reason == "env_not_allowed"
    assert states["llm"].enabled is False
    assert len(integrations.startup_warnings()) == len(integrations.GATED)  # the operator is told what the default stopped
