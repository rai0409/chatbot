from __future__ import annotations

import json

from webapi import notifications
from webapi.alerting import CRITICAL, OK, WARN


def _result(overall, signals=None):
    return {"overall": overall, "chat_requests": 100, "chat_successes": 90,
            "signals": signals or [{"name": "error_rate", "status": overall, "value": 0.3}]}


def _clear(monkeypatch):
    for var in ("ALERT_SLACK_WEBHOOK", "ALERT_SMTP_HOST", "ALERT_EMAIL_TO", "ALERT_EMAIL_FROM",
                "ALERT_SMTP_USER", "ALERT_SMTP_PASSWORD", "ALERT_SMTP_PORT", "ALERT_SMTP_STARTTLS"):
        monkeypatch.delenv(var, raising=False)


def _slack(monkeypatch):
    monkeypatch.setenv("ALERT_SLACK_WEBHOOK", "https://hooks.example/secret-token-xyz")


def _email(monkeypatch):
    monkeypatch.setenv("ALERT_SMTP_HOST", "smtp.example")
    monkeypatch.setenv("ALERT_EMAIL_TO", "ops@example")
    monkeypatch.setenv("ALERT_EMAIL_FROM", "kuraden@example")
    monkeypatch.setenv("ALERT_SMTP_PASSWORD", "smtp-password-xyz")


# --- default-off / routing --------------------------------------------------


def test_default_off_no_op(monkeypatch):
    _clear(monkeypatch)
    assert notifications.notify_enabled() is False
    out = notifications.notify(_result(CRITICAL))
    assert out["channels"] == [] and out["results"] == {}


def test_ok_severity_routes_nothing(monkeypatch):
    _clear(monkeypatch); _slack(monkeypatch); _email(monkeypatch)
    assert notifications.route_severity(OK) == []
    out = notifications.notify(_result(OK))
    assert out["channels"] == []


def test_warn_routes_slack_only(monkeypatch):
    _clear(monkeypatch); _slack(monkeypatch); _email(monkeypatch)
    assert notifications.route_severity(WARN) == ["slack"]


def test_critical_routes_slack_and_email(monkeypatch):
    _clear(monkeypatch); _slack(monkeypatch); _email(monkeypatch)
    assert notifications.route_severity(CRITICAL) == ["slack", "email"]


# --- retry/backoff (mock transports) ---------------------------------------


def test_retry_then_success(monkeypatch):
    _clear(monkeypatch); _slack(monkeypatch)
    attempts = {"n": 0}
    sleeps = []

    def flaky(webhook, text):
        attempts["n"] += 1
        if attempts["n"] < 3:
            raise RuntimeError("transient")

    out = notifications.notify(_result(WARN), slack_send=flaky, sleep=sleeps.append)
    assert out["results"]["slack"] == {"ok": True, "attempts": 3}
    assert len(sleeps) == 2  # backoff between the 3 attempts


def test_all_attempts_fail_no_raise(monkeypatch):
    _clear(monkeypatch); _slack(monkeypatch)

    def always_fail(webhook, text):
        raise RuntimeError("down")

    out = notifications.notify(_result(WARN), slack_send=always_fail, sleep=lambda s: None)
    assert out["results"]["slack"]["ok"] is False
    assert out["results"]["slack"]["attempts"] == 3


def test_critical_sends_both_channels(monkeypatch):
    _clear(monkeypatch); _slack(monkeypatch); _email(monkeypatch)
    sent = {"slack": 0, "email": 0}
    out = notifications.notify(
        _result(CRITICAL),
        slack_send=lambda w, t: sent.__setitem__("slack", sent["slack"] + 1),
        email_send=lambda s, b: sent.__setitem__("email", sent["email"] + 1),
        sleep=lambda s: None,
    )
    assert sent == {"slack": 1, "email": 1}
    assert out["results"]["slack"]["ok"] and out["results"]["email"]["ok"]


# --- no secret exposure -----------------------------------------------------


def test_message_and_summary_have_no_secrets(monkeypatch):
    _clear(monkeypatch); _slack(monkeypatch); _email(monkeypatch)
    captured = {}
    out = notifications.notify(
        _result(CRITICAL),
        slack_send=lambda w, t: captured.update(slack_text=t),
        email_send=lambda s, b: captured.update(email_body=b, email_subject=s),
        sleep=lambda s: None,
    )
    blob = json.dumps(out) + captured.get("slack_text", "") + captured.get("email_body", "")
    # webhook token + smtp password must never appear in message or summary
    assert "secret-token-xyz" not in blob
    assert "smtp-password-xyz" not in blob
    for forbidden in ("sk-", "Bearer ", "X-Api-Key"):
        assert forbidden not in blob


def test_build_message_only_safe_fields(monkeypatch):
    msg = notifications.build_message(_result(CRITICAL, signals=[
        {"name": "error_rate", "status": CRITICAL, "value": 0.3},
        {"name": "fallback_rate", "status": OK, "value": 0.0},
    ]))
    assert "error_rate" in msg and "CRITICAL" in msg
    # OK signals are not spammed into the message
    assert "fallback_rate" not in msg
