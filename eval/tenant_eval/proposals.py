"""Proposed changes to a tenant's retrieval profile or settings, and the rules for moving them along."""
from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional

import config
from eval.tenant_eval import ledger
from rag_core import knob_registry, profile_store

SCHEMA = 1
STATES = (
    "observed", "classified", "proposed", "evaluated", "approved", "shadow", "canary", "live", "monitoring",
    "retired", "rejected",
)
TERMINAL = {"retired", "rejected"}
_FORWARD = {
    "observed": "classified", "classified": "proposed", "proposed": "evaluated", "evaluated": "approved",
    "approved": "shadow", "shadow": "canary", "canary": "live", "live": "monitoring", "monitoring": "retired",
}
_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
PROFILE_CATEGORIES = ("synonym", "boost_rule", "validation_rule")
KNOB_CATEGORIES = ("guard_threshold", "retrieval_setting")
MANUAL_CATEGORIES = ("ingest_gap", "stale_content", "other")
CATEGORIES = PROFILE_CATEGORIES + KNOB_CATEGORIES + MANUAL_CATEGORIES


class ProposalError(ValueError):
    """The move is not allowed, or the input is not valid."""


def store_dir() -> Path:
    raw = str(os.getenv("TENANT_EVAL_PROPOSALS_DIR", "")).strip()
    return Path(raw) if raw else Path(config.RUNS_DIR) / "tenant_eval" / "proposals"


def _path(proposal_id: Any) -> Path:
    if not isinstance(proposal_id, str) or not _ID_RE.fullmatch(proposal_id):
        raise ProposalError("invalid proposal id (letters, digits, '_' and '-', at most 64)")
    return store_dir() / f"{proposal_id}.json"


def _now(now: Optional[datetime]) -> str:
    return (now or datetime.now(timezone.utc)).isoformat()


def _actor(value: Any) -> str:
    text = str(value or "").strip()
    if not text or len(text) > 64:
        raise ProposalError("an actor (1 to 64 characters) is required")
    return text


def _text(value: Any, what: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ProposalError(f"{what} is required")
    return text[:2000]


# --- storage --------------------------------------------------------------------------------------


def get(proposal_id: str) -> Dict[str, Any]:
    path = _path(proposal_id)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise ProposalError(f"proposal '{proposal_id}' not found") from None
    except (OSError, ValueError) as exc:
        raise ProposalError(f"proposal '{proposal_id}' is unreadable") from exc
    if not isinstance(data, dict) or data.get("schema") != SCHEMA or data.get("state") not in STATES:
        raise ProposalError(f"proposal '{proposal_id}' has an unsupported format")
    return data


def list_proposals(state: Optional[str] = None) -> List[Dict[str, Any]]:
    root = store_dir()
    if not root.is_dir():
        return []
    rows = []
    for path in sorted(root.glob("*.json")):
        if _ID_RE.fullmatch(path.stem):
            proposal = get(path.stem)
            if state is None or proposal["state"] == state:
                rows.append(proposal)
    return rows


def _save(proposal: Dict[str, Any], *, expected_rev: int) -> None:
    path = _path(proposal["id"])
    if path.exists() and get(proposal["id"])["rev"] != expected_rev:
        raise ProposalError(f"proposal '{proposal['id']}' was changed by someone else; reload it")
    proposal["rev"] = expected_rev + 1
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    tmp.write_text(json.dumps(proposal, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def _move(proposal: Dict[str, Any], to: str, actor: str, note: str, now: Optional[datetime], **evidence: Any) -> None:
    proposal["history"].append(
        {"at": _now(now), "actor": actor, "from": proposal["state"], "to": to, "note": note, "evidence": evidence}
    )
    proposal["state"] = to


def _need_state(proposal: Dict[str, Any], *allowed: str) -> None:
    if proposal["state"] not in allowed:
        raise ProposalError(
            f"proposal '{proposal['id']}' is {proposal['state']}; this move needs {' or '.join(allowed)}"
        )


# --- the moves ------------------------------------------------------------------------------------


def create(
    proposal_id: str, *, profile_id: str, title: str, actor: str, source: Optional[Mapping[str, Any]] = None,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """A new proposal in state "observed" (a problem was seen; `source` says where, e.g. feedback counts)."""
    path = _path(proposal_id)
    if path.exists():
        raise ProposalError(f"proposal '{proposal_id}' already exists")
    if not _ID_RE.fullmatch(str(profile_id or "")):
        raise ProposalError("invalid profile id")
    proposal: Dict[str, Any] = {
        "schema": SCHEMA, "id": proposal_id, "rev": 0, "state": "observed", "profile_id": profile_id,
        "title": _text(title, "a title"), "source": dict(source or {}), "category": None, "change": None,
        "proposed_by": None, "evaluation": None, "approvals": [], "history": [],
    }
    proposal["history"].append(
        {"at": _now(now), "actor": _actor(actor), "from": None, "to": "observed", "note": "created", "evidence": {}}
    )
    _save(proposal, expected_rev=-1)
    return proposal


def classify(proposal_id: str, *, actor: str, category: str, note: str = "", now: Optional[datetime] = None) -> Dict[str, Any]:
    proposal = get(proposal_id)
    _need_state(proposal, "observed")
    if category not in CATEGORIES:
        raise ProposalError(f"unknown category '{category}' (one of: {', '.join(CATEGORIES)})")
    proposal["category"] = category
    _move(proposal, "classified", _actor(actor), note, now, category=category)
    _save(proposal, expected_rev=proposal["rev"])
    return proposal


def declared_change(change: Mapping[str, Any]) -> Dict[str, Any]:
    """The change as tenant_eval records it as "declared" (the match between a proposal and its evaluation)."""
    declared: Dict[str, Any] = {}
    if change.get("knobs"):
        declared["knobs"] = dict(change["knobs"])
    if change.get("profile_version"):
        declared["profile_version"] = change["profile_version"]
    return declared


def propose(
    proposal_id: str, *, actor: str, change: Mapping[str, Any], note: str = "", now: Optional[datetime] = None
) -> Dict[str, Any]:
    """Attach the change: {"profile_version": V} and/or {"knobs": {...}}; or {"manual": "text"} for manual categories."""
    proposal = get(proposal_id)
    _need_state(proposal, "classified", "evaluated")
    category = proposal["category"]
    if category in MANUAL_CATEGORIES:
        if set(change) != {"manual"}:
            raise ProposalError(f"a '{category}' proposal is manual: give {{\"manual\": \"what to do\"}} only")
        clean: Dict[str, Any] = {"manual": _text(change["manual"], "the manual description")}
    else:
        if "manual" in change or not set(change) <= {"profile_version", "knobs"}:
            raise ProposalError("a change is a profile_version and/or knobs")
        clean = {}
        if category in PROFILE_CATEGORIES and not change.get("profile_version"):
            raise ProposalError(f"a '{category}' change needs a profile_version")
        if category in KNOB_CATEGORIES and not change.get("knobs"):
            raise ProposalError(f"a '{category}' change needs knobs")
        if change.get("profile_version"):
            try:
                profile_store.verify_version(proposal["profile_id"], str(change["profile_version"]))
            except profile_store.ProfileStoreError as exc:
                raise ProposalError(f"profile version: {exc}") from exc
            clean["profile_version"] = str(change["profile_version"])
        if change.get("knobs"):
            clean["knobs"] = knob_registry.check_change(change["knobs"])
    revised = proposal["state"] == "evaluated"
    proposal.update(change=clean, proposed_by=_actor(actor), evaluation=None, approvals=[])
    _move(proposal, "proposed", proposal["proposed_by"], note or ("revised" if revised else ""), now, change=clean)
    _save(proposal, expected_rev=proposal["rev"])
    return proposal


def attach_evaluation(
    proposal_id: str, *, actor: str, ledger_seq: int, ledger_file: Optional[Path] = None, now: Optional[datetime] = None
) -> Dict[str, Any]:
    """Record an evaluation: a ledger entry of this proposal, for exactly this change, on an intact ledger."""
    proposal = get(proposal_id)
    _need_state(proposal, "proposed")
    if "manual" in proposal["change"]:
        raise ProposalError("a manual proposal cannot be evaluated here; close it when it is done")
    try:
        ledger.verify_chain(ledger_file)
        entries = ledger.read_ledger(ledger_file)
    except ledger.LedgerError as exc:
        raise ProposalError(f"evaluation ledger: {exc}") from exc
    entry = next((row for row in entries if row.get("seq") == ledger_seq), None)
    if entry is None:
        raise ProposalError(f"the ledger has no entry {ledger_seq}")
    if entry.get("proposal_id") != proposal_id:
        raise ProposalError(f"ledger entry {ledger_seq} is an evaluation of another proposal")
    if (entry.get("conditions") or {}).get("declared") != declared_change(proposal["change"]):
        raise ProposalError(f"ledger entry {ledger_seq} evaluated a different change than the one proposed")
    proposal["evaluation"] = {
        "ledger_seq": ledger_seq, "run_id": entry.get("run_id"), "decision": entry.get("decision"),
        "eval_set_sha256": entry.get("eval_set_sha256"), "entry_sha256": entry.get("sha256"),
    }
    _move(proposal, "evaluated", _actor(actor), "", now, ledger_seq=ledger_seq, decision=entry.get("decision"))
    _save(proposal, expected_rev=proposal["rev"])
    return proposal


def approvals_required(proposal: Mapping[str, Any]) -> int:
    """2 when the change sets a high-risk knob (a distance guard), else 1."""
    knobs = (proposal.get("change") or {}).get("knobs") or {}
    return 2 if any(knob_registry.knob(name).risk == "high" for name in knobs) else 1


def approve(
    proposal_id: str, *, actor: str, note: str = "", accept_insufficient_reason: Optional[str] = None,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    proposal = get(proposal_id)
    _need_state(proposal, "evaluated")
    who = _actor(actor)
    if who == proposal["proposed_by"]:
        raise ProposalError("the proposer cannot approve their own change")
    if any(item["actor"] == who for item in proposal["approvals"]):
        raise ProposalError(f"{who} has already approved this change")
    decision = proposal["evaluation"]["decision"]
    reason = ""
    if decision == "fail":
        raise ProposalError("the evaluation failed; a failed change cannot be approved (revise it and evaluate again)")
    if decision == "insufficient":
        reason = str(accept_insufficient_reason or "").strip()
        if not reason:
            raise ProposalError("the evaluation was insufficient; approving needs accept_insufficient_reason")
    elif decision != "pass":
        raise ProposalError(f"the evaluation has an unknown decision '{decision}'")
    proposal["approvals"].append(
        {"actor": who, "at": _now(now), "note": note, "accepted_insufficient": reason or None}
    )
    required = approvals_required(proposal)
    if len(proposal["approvals"]) >= required:
        _move(proposal, "approved", who, note, now, approvals=len(proposal["approvals"]), required=required,
              accepted_insufficient=reason or None)
    _save(proposal, expected_rev=proposal["rev"])
    return proposal


def reject(proposal_id: str, *, actor: str, reason: str, now: Optional[datetime] = None) -> Dict[str, Any]:
    proposal = get(proposal_id)
    _need_state(proposal, "observed", "classified", "proposed", "evaluated")
    _move(proposal, "rejected", _actor(actor), _text(reason, "a reason"), now)
    _save(proposal, expected_rev=proposal["rev"])
    return proposal


def advance(proposal_id: str, *, actor: str, evidence: str, now: Optional[datetime] = None) -> Dict[str, Any]:
    """approved -> shadow -> canary -> live -> monitoring, one step, with written evidence for the step."""
    proposal = get(proposal_id)
    _need_state(proposal, "approved", "shadow", "canary", "live")
    target = _FORWARD[proposal["state"]]
    _move(proposal, target, _actor(actor), "", now, text=_text(evidence, "evidence"))
    _save(proposal, expected_rev=proposal["rev"])
    return proposal


def retire(proposal_id: str, *, actor: str, reason: str, now: Optional[datetime] = None) -> Dict[str, Any]:
    """Close a proposal: done, aborted or rolled back. Possible from approved on, and for manual proposals."""
    proposal = get(proposal_id)
    manual = "manual" in (proposal.get("change") or {})
    _need_state(proposal, "proposed", "approved", "shadow", "canary", "live", "monitoring")
    if proposal["state"] == "proposed" and not manual:
        raise ProposalError("an unevaluated change is rejected, not retired")
    _move(proposal, "retired", _actor(actor), _text(reason, "a reason"), now)
    _save(proposal, expected_rev=proposal["rev"])
    return proposal
