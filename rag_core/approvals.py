"""Two-person approval of a change that widens what may leave the system (S7).

Turning an external integration on is a decision about where company data may go. With INTEGRATION_APPROVALS_REQUIRED=2 (the
default) one person asks and a different person approves: until then nothing changes. Turning an integration off, or taking a tenant
out, never waits: it narrows what may leave.

A request is kept with who asked (a name, and a single sign-on identifier when there is one), why, and when it expires
(APPROVAL_EXPIRY_HOURS). It can be approved, rejected by someone else, or cancelled by whoever asked. Approving applies the change through
the same function as an immediate change (integrations.set_switch), with both names on the change; everything is written to the audit log.

"A different person" is only as strong as the identification: a name typed in the console can be any name. With a single sign-on session
the identifiers are compared, and with APPROVAL_REQUIRE_SSO=true only identified people may approve. The same person cannot approve their own
request in any case: by identifier when both are identified, else by name (case and spaces ignored).
"""
from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional

import config
from rag_core import audit_log, runtime_settings

PENDING, APPROVED, REJECTED, CANCELLED, EXPIRED = "pending", "approved", "rejected", "cancelled", "expired"
STATUSES = (PENDING, APPROVED, REJECTED, CANCELLED, EXPIRED)
DECISIONS = ("approve", "reject", "cancel")
KIND_ENABLE = "integration_enable"
MAX_ITEMS = 500
FILE_NAME = "approvals.json"


class ApprovalError(ValueError):
    """The request or the decision is not allowed (the message says why)."""


def required() -> int:
    """How many people a widening change needs (1: at once, 2 or more: a second person approves)."""
    try:
        return max(1, int(getattr(config, "INTEGRATION_APPROVALS_REQUIRED", 2)))
    except (TypeError, ValueError):
        return 2


def _path() -> Path:
    return runtime_settings._dir() / FILE_NAME


def _now(now: Optional[datetime]) -> datetime:
    return now or datetime.now(timezone.utc)


def _norm(name: str) -> str:
    return "".join(str(name or "").split()).casefold()


def same_person(a: Mapping[str, Any], b: Mapping[str, Any]) -> bool:
    """Are two identities one person? Identifiers when both have one, else the names (case and spaces ignored)."""
    if a.get("id") and b.get("id"):
        return a["id"] == b["id"]
    return _norm(a.get("name", "")) == _norm(b.get("name", ""))


def _load() -> Dict[str, Any]:
    try:
        data = json.loads(_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"rev": 0, "items": []}
    return data if isinstance(data, dict) and isinstance(data.get("items"), list) else {"rev": 0, "items": []}


def _save(data: Dict[str, Any]) -> None:
    path = _path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + f".{os.getpid()}.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    os.replace(tmp, path)


def _expire(items: List[Dict[str, Any]], now: datetime) -> bool:
    changed = False
    for item in items:
        if item["status"] == PENDING and datetime.fromisoformat(item["expires_at"]) <= now:
            item.update(status=EXPIRED, decided_at=now.isoformat(), decision_reason="not decided in time")
            changed = True
    return changed


def list_items(status: Optional[str] = None, *, now: Optional[datetime] = None) -> List[Dict[str, Any]]:
    """The requests, newest first (a pending one past its time is shown as expired)."""
    clock = _now(now)
    items = [dict(i) for i in _load()["items"]]
    for item in items:
        if item["status"] == PENDING and datetime.fromisoformat(item["expires_at"]) <= clock:
            item["status"] = EXPIRED
    if status:
        items = [i for i in items if i["status"] == status]
    return list(reversed(items))


def pending_count(*, now: Optional[datetime] = None) -> int:
    return len(list_items(PENDING, now=now))


def get(item_id: str, *, now: Optional[datetime] = None) -> Dict[str, Any]:
    for item in list_items(now=now):
        if item["id"] == item_id:
            return item
    raise ApprovalError(f"request '{item_id}' not found")


def request_change(kind: str, params: Mapping[str, Any], *, reason: str, requested_by: Mapping[str, Any], now: Optional[datetime] = None) -> Dict[str, Any]:
    """Ask for a change. The same change cannot be asked for twice while a request for it is pending."""
    if kind != KIND_ENABLE:
        raise ApprovalError("this kind of request is not known")
    clean_reason = str(reason or "").strip()
    if not clean_reason or len(clean_reason) > 300:
        raise ApprovalError("a reason (1-300 characters) is required")
    clock = _now(now)
    path = _path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with audit_log._FileLock(path):
        data = _load()
        _expire(data["items"], clock)
        for item in data["items"]:
            if item["status"] == PENDING and item["kind"] == kind and item["params"] == dict(params):
                raise ApprovalError("a request for this change is already waiting for approval")
        item = {"id": "apr-" + uuid.uuid4().hex[:12], "kind": kind, "params": dict(params), "reason": clean_reason,
                "requested_by": dict(requested_by), "requested_at": clock.isoformat(),
                "expires_at": (clock + timedelta(hours=float(getattr(config, "APPROVAL_EXPIRY_HOURS", 72.0)))).isoformat(),
                "status": PENDING, "decided_by": None, "decided_at": None, "decision_reason": None, "applied_rev": None}
        data["items"] = (data["items"] + [item])[-MAX_ITEMS:]
        data["rev"] = int(data["rev"]) + 1
        _save(data)
    audit_log.append_audit_event("approvals", {"action": "approval_requested", "id": item["id"], "kind": kind, "params": item["params"],
                                               "requested_by": requested_by.get("name"), "verified": bool(requested_by.get("verified"))})
    return item


def _apply_enable(item: Mapping[str, Any], approver: Mapping[str, Any]) -> Dict[str, Any]:
    from rag_core import integrations

    who = f"{item['requested_by']['name']} (asked) / {approver['name']} (approved)"
    return integrations.set_switch(item["params"]["name"], True, by=who, reason=item["reason"], tenant_id=item["params"].get("tenant_id"))


def decide(item_id: str, decision: str, *, decided_by: Mapping[str, Any], reason: str = "", now: Optional[datetime] = None,
           apply: Optional[Callable[[Mapping[str, Any], Mapping[str, Any]], Dict[str, Any]]] = None) -> Dict[str, Any]:
    """Approve, reject or cancel a pending request.

    approve: a different person, identified by single sign-on when APPROVAL_REQUIRE_SSO; the change is applied first and the request is
    marked only when that worked. reject: a different person. cancel: whoever asked."""
    if decision not in DECISIONS:
        raise ApprovalError(f"the decision is one of: {', '.join(DECISIONS)}")
    clock = _now(now)
    path = _path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with audit_log._FileLock(path):
        data = _load()
        if _expire(data["items"], clock):
            _save(data)
        item = next((i for i in data["items"] if i["id"] == item_id), None)
        if item is None:
            raise ApprovalError(f"request '{item_id}' not found")
        if item["status"] != PENDING:
            raise ApprovalError(f"the request is already {item['status']}")
        asker = item["requested_by"]
        if decision == "cancel":
            if not same_person(asker, decided_by):
                raise ApprovalError("only the person who asked can cancel the request")
        else:
            if same_person(asker, decided_by):
                raise ApprovalError("the person who asked cannot approve or reject their own request (cancel it instead)")
            if getattr(config, "APPROVAL_REQUIRE_SSO", False) and not (asker.get("verified") and decided_by.get("verified")):
                raise ApprovalError("both people must be identified by single sign-on (APPROVAL_REQUIRE_SSO)")
        applied: Optional[Dict[str, Any]] = None
        if decision == "approve":
            applied = (apply or _apply_enable)(item, decided_by)
        status = {"approve": APPROVED, "reject": REJECTED, "cancel": CANCELLED}[decision]
        item.update(status=status, decided_by=dict(decided_by), decided_at=clock.isoformat(), decision_reason=str(reason or "")[:300] or None,
                    applied_rev=(applied or {}).get("rev"))
        data["rev"] = int(data["rev"]) + 1
        _save(data)
        result = dict(item)
    audit_log.append_audit_event("approvals", {"action": f"approval_{status}", "id": item_id, "kind": result["kind"], "params": result["params"],
                                               "requested_by": asker.get("name"), "decided_by": decided_by.get("name"),
                                               "verified": bool(asker.get("verified") and decided_by.get("verified"))})
    return result
