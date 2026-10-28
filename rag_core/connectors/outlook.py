"""Outlook drafts through Microsoft Graph: drafts only, nothing is sent (step 7-2)."""
from __future__ import annotations

import re
import urllib.parse
from datetime import datetime
from typing import Any, Dict, Mapping, Optional, Sequence, Tuple

from rag_core import contracts
from rag_core.connectors.sharepoint import GRAPH, GraphClient, token_provider

_MAILBOX_RE = re.compile(r"^([^@\s]{1,64}@[^@\s]{1,255}\.[^@\s]{2,}|[0-9a-fA-F-]{36})$")
MAX_RECIPIENTS = 10


def default_client() -> GraphClient:
    """A Graph client whose token comes from the OUTLOOK_* settings (a fixed token, or client credentials)."""
    import os

    mapped = {"SHAREPOINT_" + name: os.environ.get("OUTLOOK_" + name, "")
              for name in ("ACCESS_TOKEN", "TENANT_ID", "CLIENT_ID", "CLIENT_SECRET")}
    return GraphClient(token_provider(mapped))


def create_draft(client: GraphClient, mailbox: str, *, to: Sequence[str], subject: str, body: str) -> Dict[str, str]:
    """Create a draft in `mailbox` (never send it). Returns {"draft_id", "web_link"}."""
    if not _MAILBOX_RE.fullmatch(str(mailbox or "")):
        raise contracts.ContractError("the mailbox must be an e-mail address or an object id")
    recipients = [str(a).strip() for a in to]
    if not recipients or len(recipients) > MAX_RECIPIENTS or any(not contracts._EMAIL_RE.fullmatch(a) for a in recipients):
        raise contracts.ContractError(f"1 to {MAX_RECIPIENTS} valid recipient addresses are required")
    if not subject.strip() or len(subject) > 200 or len(body) > 20000:
        raise contracts.ContractError("the subject (1-200) or the body (at most 20000 characters) is not valid")
    payload = {"subject": subject, "body": {"contentType": "Text", "content": body},
               "toRecipients": [{"emailAddress": {"address": a}} for a in recipients]}
    answer = client.post_json(f"{GRAPH}/users/{urllib.parse.quote(mailbox)}/messages", payload)
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


def draft_for_deadline(
    tenant_id: str, contract_id: str, kind: str, *, client: GraphClient, mailbox: str, by: str, now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """A draft notice to the counterparty for one deadline. Needs a confirmed address and a deadline on confirmed values."""
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
    subject, body = compose_notice(contract, deadline)
    draft = create_draft(client, mailbox, to=[email["value"]], subject=subject, body=body)
    contracts._log(contract, who, "draft_created", kind, None, draft["draft_id"], now)
    contracts._save(contract, expected_rev=contract["rev"])
    return {**draft, "kind": kind, "date": deadline["date"]}
