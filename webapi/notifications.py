from __future__ import annotations

# Optional alert notifications (Prompt051): Slack webhook + SMTP email for the
# alert-checker output, with severity routing and retry/backoff. Default-off;
# enabled only when the relevant env vars are set. Secrets (webhook URL, SMTP
# password) are ENV-ONLY and are never logged or returned. Uses only the stdlib
# (urllib, smtplib) — no new dependencies. Transports are injectable so tests
# run fully offline with mocks.

import json
import os
import smtplib
import time
import urllib.request
from email.message import EmailMessage
from typing import Any, Callable, Dict, List, Optional

from webapi.alerting import CRITICAL, OK, WARN

_MAX_ATTEMPTS = 3
_BASE_BACKOFF = 0.5


def _env(name: str) -> str:
    return str(os.getenv(name, "") or "").strip()


def slack_configured() -> bool:
    return bool(_env("ALERT_SLACK_WEBHOOK"))


def email_configured() -> bool:
    return bool(_env("ALERT_SMTP_HOST") and _env("ALERT_EMAIL_TO") and _env("ALERT_EMAIL_FROM"))


def notify_enabled() -> bool:
    return slack_configured() or email_configured()


def route_severity(overall: str) -> List[str]:
    # OK -> nothing; WARN -> slack; CRITICAL -> slack + email.
    channels: List[str] = []
    if overall == WARN:
        if slack_configured():
            channels.append("slack")
    elif overall == CRITICAL:
        if slack_configured():
            channels.append("slack")
        if email_configured():
            channels.append("email")
    return channels


def build_message(result: Dict[str, Any]) -> str:
    # Safe text: overall + per-signal status/value only. The alert result holds
    # nothing but enum signal names and integer/float metrics — no prompts,
    # document text, keys, or tenant data.
    lines = [f"KuraDen alert: {result.get('overall')} "
             f"(chat_requests={result.get('chat_requests')}, "
             f"chat_successes={result.get('chat_successes')})"]
    for s in result.get("signals", []):
        if s.get("status") in (WARN, CRITICAL):
            val = s.get("value")
            lines.append(f"- [{s['status']}] {s['name']}" + (f" = {val}" if val is not None else ""))
    return "\n".join(lines)


# --- transports (injectable) -----------------------------------------------


def _default_slack_send(webhook: str, text: str) -> None:
    data = json.dumps({"text": text}).encode("utf-8")
    req = urllib.request.Request(webhook, data=data, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=10) as resp:  # noqa: S310 (webhook is operator-set)
        if resp.status >= 300:
            raise RuntimeError(f"slack http {resp.status}")


def _default_email_send(subject: str, body: str) -> None:
    host = _env("ALERT_SMTP_HOST")
    port = int(_env("ALERT_SMTP_PORT") or "587")
    user = _env("ALERT_SMTP_USER")
    password = _env("ALERT_SMTP_PASSWORD")  # secret, never logged
    use_tls = (_env("ALERT_SMTP_STARTTLS") or "true").lower() in {"1", "true", "yes", "on"}
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = _env("ALERT_EMAIL_FROM")
    msg["To"] = _env("ALERT_EMAIL_TO")
    msg.set_content(body)
    with smtplib.SMTP(host, port, timeout=15) as smtp:
        if use_tls:
            smtp.starttls()
        if user and password:
            smtp.login(user, password)
        smtp.send_message(msg)


def _with_retry(fn: Callable[[], None], *, sleep: Callable[[float], None]) -> Dict[str, Any]:
    last_error = None
    for attempt in range(1, _MAX_ATTEMPTS + 1):
        try:
            fn()
            return {"ok": True, "attempts": attempt}
        except Exception as exc:  # noqa: BLE001
            # Never include the exception detail verbatim (could echo a URL);
            # record only the type.
            last_error = type(exc).__name__
            if attempt < _MAX_ATTEMPTS:
                sleep(_BASE_BACKOFF * (2 ** (attempt - 1)))
    return {"ok": False, "attempts": _MAX_ATTEMPTS, "error_type": last_error}


def notify(
    result: Dict[str, Any],
    *,
    slack_send: Optional[Callable[[str, str], None]] = None,
    email_send: Optional[Callable[[str, str], None]] = None,
    sleep: Callable[[float], None] = time.sleep,
) -> Dict[str, Any]:
    # Best-effort: never raises. Returns a secret-free summary. OK -> no-op.
    overall = str(result.get("overall") or OK)
    channels = route_severity(overall)
    summary: Dict[str, Any] = {"overall": overall, "channels": channels, "results": {}}
    if not channels:
        return summary

    text = build_message(result)
    subject = f"[KuraDen][{overall}] alert"

    if "slack" in channels:
        webhook = _env("ALERT_SLACK_WEBHOOK")
        sender = slack_send or _default_slack_send
        summary["results"]["slack"] = _with_retry(lambda: sender(webhook, text), sleep=sleep)
    if "email" in channels:
        sender = email_send or _default_email_send
        summary["results"]["email"] = _with_retry(lambda: sender(subject, text), sleep=sleep)
    return summary
