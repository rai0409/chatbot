"""How a change to the retrieval profile or the settings is proposed, judged, approved and rolled out (S4).

  GET  /admin/proposals                 the proposals (filter by state), with who proposed and approved
  GET  /admin/proposals/{id}            one proposal with its whole history
  POST /admin/proposals                 a new proposal (observed)
  POST /admin/proposals/{id}/{move}     classify | propose | attach-evaluation | approve | reject | advance | retire
  GET  /admin/profiles                  the profiles that have stored versions, and which version is active
  GET  /admin/profiles/{id}/versions    the versions and the history of the active pointer
  GET  /admin/profiles/{id}/diff        two versions compared
  POST /admin/profiles/{id}/activate    make a version the active one (reason required)
  POST /admin/profiles/{id}/rollback    back to the previous active version
  GET  /admin/evaluations               the evaluation ledger (newest first) and whether its chain is intact
  GET  /admin/knobs                     the settings an evaluation may change, with their limits and the value in force
  GET  /admin/monitoring                feedback by day and the counts of the records, to watch a change that is live

Reading is open to every administrator. Anything that changes the installation (a proposal, a version, a rollout) is the
installation administrator's. A change that needs a person (a proposal, an approval) takes a name: the same person cannot
propose and approve the same change. It is a statement of who did it, kept in the record and the audit log.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from eval.tenant_eval import ledger, proposals
from rag_core import audit_view, knob_registry, profile_store, records
from webapi import admin_common
from webapi.admin_auth import require_admin_auth, require_global

router = APIRouter()
MOVES = ("classify", "propose", "attach-evaluation", "approve", "reject", "advance", "retire")


class NewProposal(BaseModel):
    id: str = Field(..., max_length=64)
    profile_id: str = Field(..., max_length=64)
    title: str = Field(..., min_length=1, max_length=200)
    actor: str = Field(..., max_length=64)
    source: Optional[Dict[str, Any]] = None


class Move(BaseModel):
    actor: str = Field(..., max_length=64)
    note: str = Field("", max_length=1000)
    category: Optional[str] = Field(None, max_length=40)
    change: Optional[Dict[str, Any]] = None
    ledger_seq: Optional[int] = None
    accept_insufficient_reason: Optional[str] = Field(None, max_length=1000)
    reason: Optional[str] = Field(None, max_length=1000)
    evidence: Optional[str] = Field(None, max_length=2000)


class Activate(BaseModel):
    version: str = Field(..., max_length=32)
    actor: str = Field(..., max_length=64)
    reason: str = Field(..., min_length=1, max_length=300)


class Rollback(BaseModel):
    actor: str = Field(..., max_length=64)
    reason: str = Field(..., min_length=1, max_length=300)


def _proposal_error(exc: Exception) -> HTTPException:
    text = str(exc)
    code = 404 if "not found" in text else 409 if ("already exists" in text or "someone else" in text) else 400
    return HTTPException(status_code=code, detail=text)


def _summary(p: Dict[str, Any]) -> Dict[str, Any]:
    last = (p.get("history") or [{}])[-1]
    return {"id": p["id"], "title": p["title"], "state": p["state"], "category": p.get("category"), "profile_id": p["profile_id"],
            "proposed_by": p.get("proposed_by"), "approvals": [a["actor"] for a in p.get("approvals") or []],
            "approvals_required": proposals.approvals_required(p) if p.get("change") else None,
            "decision": (p.get("evaluation") or {}).get("decision"), "rev": p["rev"], "updated_at": last.get("at")}


def _profile_error(exc: Exception) -> HTTPException:
    text = str(exc)
    return HTTPException(status_code=404 if "not found" in text else 400, detail=text)


# --- proposals -------------------------------------------------------------------------------------------------------------------


@router.get("/admin/proposals")
def list_proposals(state: Optional[str] = None, _admin_auth: None = Depends(require_admin_auth)):
    if state is not None and state not in proposals.STATES:
        raise HTTPException(status_code=400, detail=f"'state' is one of: {', '.join(proposals.STATES)}")
    try:
        rows = [_summary(p) for p in proposals.list_proposals(state)]
    except proposals.ProposalError as exc:
        raise _proposal_error(exc)
    return {"states": list(proposals.STATES), "proposals": sorted(rows, key=lambda r: str(r["updated_at"]), reverse=True)}


@router.get("/admin/proposals/{proposal_id}")
def get_proposal(proposal_id: str, _admin_auth: None = Depends(require_admin_auth)):
    try:
        p = proposals.get(proposal_id)
    except proposals.ProposalError as exc:
        raise _proposal_error(exc)
    return {**p, "summary": _summary(p)}


@router.post("/admin/proposals")
def create_proposal(body: NewProposal, request: Request, _admin_auth: None = Depends(require_admin_auth)):
    scope = admin_common.request_scope(request)
    require_global(scope)
    try:
        p = proposals.create(body.id, profile_id=body.profile_id, title=body.title, actor=admin_common.actor_name(body.actor),
                             source=body.source)
    except proposals.ProposalError as exc:
        raise _proposal_error(exc)
    admin_common.audit_admin(scope, "proposal_created", proposal_id=body.id, profile_id=body.profile_id)
    return {**p, "summary": _summary(p)}


@router.post("/admin/proposals/{proposal_id}/{move}")
def move_proposal(proposal_id: str, move: str, body: Move, request: Request, _admin_auth: None = Depends(require_admin_auth)):
    scope = admin_common.request_scope(request)
    require_global(scope)
    if move not in MOVES:
        raise HTTPException(status_code=404, detail=f"a move is one of: {', '.join(MOVES)}")
    actor = admin_common.actor_name(body.actor)
    try:
        if move == "classify":
            p = proposals.classify(proposal_id, actor=actor, category=str(body.category or ""), note=body.note)
        elif move == "propose":
            p = proposals.propose(proposal_id, actor=actor, change=body.change or {}, note=body.note)
        elif move == "attach-evaluation":
            if body.ledger_seq is None:
                raise HTTPException(status_code=400, detail="ledger_seq is required")
            p = proposals.attach_evaluation(proposal_id, actor=actor, ledger_seq=body.ledger_seq)
        elif move == "approve":
            p = proposals.approve(proposal_id, actor=actor, note=body.note, accept_insufficient_reason=body.accept_insufficient_reason)
        elif move == "reject":
            p = proposals.reject(proposal_id, actor=actor, reason=str(body.reason or ""))
        elif move == "advance":
            p = proposals.advance(proposal_id, actor=actor, evidence=str(body.evidence or ""))
        else:
            p = proposals.retire(proposal_id, actor=actor, reason=str(body.reason or ""))
    except (proposals.ProposalError, knob_registry.KnobError) as exc:
        raise _proposal_error(exc)
    admin_common.audit_admin(scope, "proposal_" + move.replace("-", "_"), proposal_id=proposal_id, state=p["state"], actor=actor)
    return {**p, "summary": _summary(p)}


# --- profile versions ------------------------------------------------------------------------------------------------------------


@router.get("/admin/profiles")
def list_profiles(_admin_auth: None = Depends(require_admin_auth)):
    root = profile_store.store_root()
    rows: List[Dict[str, Any]] = []
    if root.is_dir():
        for entry in sorted(root.iterdir()):
            if not entry.is_dir():
                continue
            try:
                versions = profile_store.list_versions(entry.name)
                rows.append({"profile_id": entry.name, "versions": len(versions), "active": profile_store.pinned_version(entry.name)})
            except profile_store.ProfileStoreError:
                continue
    return {"profiles": rows}


@router.get("/admin/profiles/{profile_id}/versions")
def profile_versions(profile_id: str, _admin_auth: None = Depends(require_admin_auth)):
    try:
        versions = profile_store.list_versions(profile_id)
        pointer = profile_store._read_pointer(profile_id)
        return {"profile_id": profile_id, "active": profile_store.pinned_version(profile_id), "versions": versions,
                "history": pointer.get("history") or []}
    except profile_store.ProfileStoreError as exc:
        raise _profile_error(exc)


@router.get("/admin/profiles/{profile_id}/diff")
def profile_diff(profile_id: str, left: str, right: str, _admin_auth: None = Depends(require_admin_auth)):
    try:
        return profile_store.diff_versions(profile_id, left, right)
    except profile_store.ProfileStoreError as exc:
        raise _profile_error(exc)


@router.post("/admin/profiles/{profile_id}/activate")
def profile_activate(profile_id: str, body: Activate, request: Request, _admin_auth: None = Depends(require_admin_auth)):
    scope = admin_common.request_scope(request)
    require_global(scope)
    actor = admin_common.actor_name(body.actor)
    try:
        result = profile_store.activate(profile_id, body.version, activated_by=actor)
    except profile_store.ProfileStoreError as exc:
        raise _profile_error(exc)
    admin_common.audit_admin(scope, "profile_activated", profile_id=profile_id, version=body.version, actor=actor, reason=body.reason)
    return result


@router.post("/admin/profiles/{profile_id}/rollback")
def profile_rollback(profile_id: str, body: Rollback, request: Request, _admin_auth: None = Depends(require_admin_auth)):
    scope = admin_common.request_scope(request)
    require_global(scope)
    actor = admin_common.actor_name(body.actor)
    try:
        result = profile_store.rollback(profile_id, activated_by=actor)
    except profile_store.ProfileStoreError as exc:
        raise _profile_error(exc)
    admin_common.audit_admin(scope, "profile_rolled_back", profile_id=profile_id, actor=actor, reason=body.reason)
    return result


# --- evaluations, settings, monitoring -------------------------------------------------------------------------------------------


@router.get("/admin/evaluations")
def evaluations(limit: int = 50, _admin_auth: None = Depends(require_admin_auth)):
    try:
        intact = True
        try:
            ledger.verify_chain()
        except ledger.LedgerError:
            intact = False
        rows = ledger.read_ledger()
    except ledger.LedgerError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"chain_intact": intact, "total": len(rows), "entries": list(reversed(rows))[: max(1, min(limit, 200))]}


@router.get("/admin/knobs")
def knobs(_admin_auth: None = Depends(require_admin_auth)):
    return {"knobs": [{"name": k.name, "kind": k.kind, "stage": k.stage, "risk": k.risk, "lo": k.lo, "hi": k.hi,
                       "description": k.description, "value": knob_registry.read(k.name)} for k in knob_registry.REGISTRY]}


@router.get("/admin/monitoring")
def monitoring(request: Request, days: int = 14, _admin_auth: None = Depends(require_admin_auth)):
    scope = admin_common.request_scope(request)
    allowed = admin_common.allowed_tenants(scope)
    days = max(1, min(days, 90))
    live = []
    if scope is None:
        try:
            live = [_summary(p) for p in proposals.list_proposals() if p["state"] in ("shadow", "canary", "live", "monitoring")]
        except proposals.ProposalError:
            live = []
    return {"days": days, "feedback_by_day": audit_view.daily_counts("feedback", "feedback_type", days=days, tenants=allowed),
            "records": records.stats(days, tenants=allowed), "changes_in_rollout": live}
