"""Reminders for contract deadlines, sent once (step 7-2)."""
from __future__ import annotations

import os
from datetime import date, datetime, timezone
from typing import Any, Callable, Dict, List, Mapping, Optional, Tuple

from rag_core import contracts, integrations

DEFAULT_DAYS = (90, 60, 30, 14, 7, 1, 0)
OVERDUE = -1
OVERDUE_WINDOW_DAYS = 30
Send = Callable[[str, str], Dict[str, Any]]


def reminder_days() -> List[int]:
    """CONTRACT_REMINDER_DAYS as distinct integers 0..365, largest first; the default when it is empty or not valid."""
    raw = str(os.getenv("CONTRACT_REMINDER_DAYS", "")).strip()
    if raw:
        try:
            days = sorted({int(part) for part in raw.split(",") if part.strip()}, reverse=True)
        except ValueError:
            days = []
        if days and all(0 <= d <= 365 for d in days):
            return days
    return list(DEFAULT_DAYS)


def _outbox_file(tenant_id: str):
    return contracts._path(tenant_id, "x").with_name("_notifications.json")


def load_outbox(tenant_id: str) -> Dict[str, Any]:
    try:
        data = contracts.json.loads(_outbox_file(tenant_id).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {"sent": {}}
    except (OSError, ValueError) as exc:
        raise contracts.ContractError("the notification outbox is unreadable") from exc
    return data if isinstance(data, dict) and isinstance(data.get("sent"), dict) else {"sent": {}}


def _key(contract_id: str, kind: str, when: str, offset: int) -> str:
    return f"{contract_id}|{kind}|{when}|{offset}"


def due_reminders(tenant_id: str, today: Optional[date] = None) -> List[Dict[str, Any]]:
    """The reminders that are due and have not been sent, earliest deadline first."""
    today = today or date.today()
    sent = load_outbox(tenant_id)["sent"]
    thresholds = reminder_days()
    due: List[Dict[str, Any]] = []
    for contract in contracts.list_contracts(tenant_id):
        fields = contract["fields"]
        for item in contracts.deadlines(contract, today):
            left = item["days_left"]
            if left < 0:
                if left < -OVERDUE_WINDOW_DAYS:
                    continue
                offset = OVERDUE
            else:
                eligible = [t for t in thresholds if t >= left]
                if not eligible:
                    continue
                offset = min(eligible)
            key = _key(contract["contract_id"], item["kind"], item["date"], offset)
            if key in sent:
                continue
            due.append({**item, "key": key, "offset": offset, "contract_id": contract["contract_id"],
                        "counterparty": fields["counterparty"]["value"], "title": fields["title"]["value"],
                        "covers": [_key(contract["contract_id"], item["kind"], item["date"], t) for t in thresholds if t > offset]})
    return sorted(due, key=lambda r: (r["date"], r["contract_id"], r["kind"]))


def build_message(reminder: Mapping[str, Any]) -> Tuple[str, str]:
    left = reminder["days_left"]
    when = f"期限を {-left} 日過ぎています" if left < 0 else ("本日が期限です" if left == 0 else f"残り {left} 日")
    name = " ".join(part for part in (reminder.get("title"), reminder.get("counterparty")) if part) or reminder["contract_id"]
    subject = f"[契約期限] {name[:80]}: {reminder['label']}（{when}）"
    lines = [
        "契約の期限をお知らせします。", "",
        f"契約ID: {reminder['contract_id']}", f"相手方: {reminder.get('counterparty') or '（未確認）'}",
        f"件名: {reminder.get('title') or '（未設定）'}", f"期限: {reminder['label']}（{reminder['date']}）", f"状況: {when}", "",
    ]
    if not reminder["verified"]:
        lines.append("※この期限は、人が確認していない項目に基づいています。契約書で確認してください。")
    lines.append("※暦日で数えた目安です（休日の調整や条文の解釈はしていません）。契約書の文言が優先します。")
    return subject, "\n".join(lines)


def default_send(subject: str, body: str) -> Dict[str, Any]:
    """The existing e-mail notification. Without SMTP settings nothing is sent (and nothing is marked sent)."""
    try:
        from webapi import notifications

        if not notifications.email_configured():
            return {"ok": False, "reason": "email_not_configured"}
        notifications._default_email_send(subject, body)
        return {"ok": True}
    except integrations.IntegrationDisabled as exc:
        return {"ok": False, "reason": f"integration_off:{exc.status.reason}"}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "reason": type(exc).__name__}


def run(
    tenant_id: str, *, today: Optional[date] = None, send: Optional[Send] = None, dry_run: bool = False,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Send every due reminder once. A failure is left for the next run; the result never holds the message text."""
    sender = send or default_send
    reminders = due_reminders(tenant_id, today)
    outbox = load_outbox(tenant_id)
    results = []
    sent = failed = 0
    for reminder in reminders:
        row = {"key": reminder["key"], "contract_id": reminder["contract_id"], "kind": reminder["kind"],
               "date": reminder["date"], "offset": reminder["offset"], "verified": reminder["verified"]}
        if dry_run:
            results.append({**row, "status": "due"})
            continue
        subject, body = build_message(reminder)
        outcome = sender(subject, body)
        if outcome.get("ok"):
            stamp = (now or datetime.now(timezone.utc)).isoformat()
            outbox["sent"][reminder["key"]] = {"at": stamp}
            for covered in reminder["covers"]:
                outbox["sent"].setdefault(covered, {"at": stamp, "covered_by": reminder["key"]})
            sent += 1
            results.append({**row, "status": "sent"})
        else:
            failed += 1
            results.append({**row, "status": "failed", "reason": str(outcome.get("reason") or "send_failed")[:80]})
    if sent:
        contracts._write(_outbox_file(tenant_id), outbox)
    return {"due": len(reminders), "sent": sent, "failed": failed, "dry_run": bool(dry_run), "results": results}
