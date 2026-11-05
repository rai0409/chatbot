"""Outlook drafts through Microsoft Graph: drafts only, nothing is sent (step 7-2)."""
from __future__ import annotations

import os
import re
import urllib.parse
from datetime import datetime
from typing import Any, Dict, Mapping, Optional, Sequence, Tuple

from rag_core import contracts
from rag_core.connectors.sharepoint import GraphClient, graph_base, token_provider, transport_for

_MAILBOX_RE = re.compile(r"^([^@\s]{1,64}@[^@\s]{1,255}\.[^@\s]{2,}|[0-9a-fA-F-]{36})$")
MAX_RECIPIENTS = 10
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")


def _mailbox_allowed(mailbox: str) -> bool:
    """OUTLOOK_ALLOWED_MAILBOXES (comma separated): drafts are made only in those mailboxes (required in production)."""
    raw = str(os.environ.get("OUTLOOK_ALLOWED_MAILBOXES", "") or "")
    allowed = {part.strip().lower() for part in raw.split(",") if part.strip()}
    if not allowed:
        # With application permissions the app can reach every mailbox of the organization:
        # in production the list is what limits it to the intended ones.
        if str(os.environ.get("APP_ENV", "")).strip().lower() == "production":
            raise contracts.ContractError("OUTLOOK_ALLOWED_MAILBOXES is required in production")
        return True
    return mailbox.strip().lower() in allowed


def _check_mailbox(mailbox: str) -> None:
    if not _MAILBOX_RE.fullmatch(str(mailbox or "")):
        raise contracts.ContractError("the mailbox must be an e-mail address or an object id")
    if not _mailbox_allowed(mailbox):
        raise contracts.ContractError("the mailbox is not allowed (OUTLOOK_ALLOWED_MAILBOXES)")


def default_client() -> GraphClient:
    """A Graph client whose token comes from the OUTLOOK_* settings (a fixed token, or client credentials)."""
    import os

    mapped = {"SHAREPOINT_" + name: os.environ.get("OUTLOOK_" + name, "")
              for name in ("ACCESS_TOKEN", "TENANT_ID", "CLIENT_ID", "CLIENT_SECRET")}
    transport = transport_for("outlook_draft")  # the egress check of this integration, not SharePoint's
    return GraphClient(token_provider(mapped, transport=transport), transport=transport)


def create_draft(client: GraphClient, mailbox: str, *, to: Sequence[str], subject: str, body: str) -> Dict[str, str]:
    """Create a draft in `mailbox` (never send it). Returns {"draft_id", "web_link"}."""
    _check_mailbox(mailbox)
    recipients = [str(a).strip() for a in to]
    if not recipients or len(recipients) > MAX_RECIPIENTS or any(not contracts._EMAIL_RE.fullmatch(a) for a in recipients):
        raise contracts.ContractError(f"1 to {MAX_RECIPIENTS} valid recipient addresses are required")
    if _CONTROL_RE.search(subject):
        raise contracts.ContractError("the subject must be one line without control characters")
    if not subject.strip() or len(subject) > 200 or len(body) > 20000:
        raise contracts.ContractError("the subject (1-200) or the body (at most 20000 characters) is not valid")
    payload = {"subject": subject, "body": {"contentType": "Text", "content": body},
               "toRecipients": [{"emailAddress": {"address": a}} for a in recipients]}
    answer = client.post_json(f"{graph_base()}/users/{urllib.parse.quote(mailbox)}/messages", payload)
    return {"draft_id": str(answer.get("id") or ""), "web_link": str(answer.get("webLink") or "")}


def compose_notice(contract: Mapping[str, Any], deadline: Mapping[str, Any]) -> Tuple[str, str]:
    """A plain template for a notice about one deadline, to be read and changed by a person before it is sent."""
    fields = contract["fields"]
    name = fields["title"]["value"] or contract["contract_id"]
    subject = f"{name} に関するご連絡"
    body = "\n".join([
        f"{fields['counterparty']['value'] or ''} ご担当者様", "",
        f"お世話になっております。「{name}」について、{deadline['label']}（{deadline['date']}）に関するご連絡です。", "",
        "（ここに用件を書いてください。この文章は下書きのひな形です。契約書の文言を確認のうえ、内容を修正してから送信してください。）", "",
    ])
    return subject, body


def _recorded_draft(contract: Mapping[str, Any], kind: str, date: str) -> Optional[str]:
    """The draft id the history holds for this deadline (kind and date), if a draft was already made."""
    for entry in reversed(contract.get("history") or []):
        if entry.get("action") == "draft_created" and entry.get("field") == kind and entry.get("old") == date:
            return entry.get("new")
    return None


def draft_for_deadline(
    tenant_id: str, contract_id: str, kind: str, *, client: GraphClient, mailbox: str, by: str,
    now: Optional[datetime] = None, force_new: bool = False,
) -> Dict[str, Any]:
    """A draft notice to the counterparty for one deadline. Needs a confirmed address and a deadline on confirmed values.

    Idempotent: when the history already holds a draft for this deadline (same kind and date), that draft is returned
    ("reused": True) and nothing is created, so a retry or a double click does not fill the mailbox with copies.
    force_new=True makes another one on purpose."""
    _check_mailbox(mailbox)  # before anything else, also when an earlier draft would be reused
    who = contracts._who(by)
    contract = contracts.get(tenant_id, contract_id)
    email = contract["fields"]["contact_email"]
    if email["value"] is None or email["state"] not in contracts.DECIDED:
        raise contracts.ContractError("the contact e-mail has not been confirmed by a person")
    deadline = next((d for d in contracts.deadlines(contract) if d["kind"] == kind), None)
    if deadline is None:
        raise contracts.ContractError(f"the contract has no '{kind}' deadline")
    if not deadline["verified"]:
        raise contracts.ContractError("the deadline rests on values that have not been confirmed")
    existing = None if force_new else _recorded_draft(contract, kind, deadline["date"])
    if existing:
        return {"draft_id": existing, "web_link": "", "kind": kind, "date": deadline["date"], "reused": True}
    subject, body = compose_notice(contract, deadline)
    draft = create_draft(client, mailbox, to=[email["value"]], subject=subject, body=body)
    contracts._log(contract, who, "draft_created", kind, deadline["date"], draft["draft_id"], now)
    try:
        contracts._save(contract, expected_rev=contract["rev"])
    except contracts.ContractError:
        # The draft exists in the mailbox already: reload, record it, save once more (else a retry would make a copy).
        fresh = contracts.get(tenant_id, contract_id)
        contracts._log(fresh, who, "draft_created", kind, deadline["date"], draft["draft_id"], now)
        contracts._save(fresh, expected_rev=fresh["rev"])
    return {**draft, "kind": kind, "date": deadline["date"]}
