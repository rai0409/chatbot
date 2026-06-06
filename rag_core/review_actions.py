from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

import config


ALLOWED_ACTION_TYPES = {
    "approve_candidate",
    "reject_candidate",
    "needs_new_faq",
    "needs_document_update",
    "needs_policy_review",
    "mark_resolved",
    "mark_ignored",
}
ALLOWED_STATUS_AFTER = {
    "open",
    "in_review",
    "resolved",
    "ignored",
    "needs_followup",
}

_MAX_STRING_CHARS = 1000
_MAX_TAGS = 20


def _bounded_text(value: Any, max_chars: int = _MAX_STRING_CHARS) -> str | None:
    if value is None:
        return None
    text = str(value)
    if len(text) <= max_chars:
        return text
    suffix = "...[truncated]"
    if max_chars <= len(suffix):
        return text[:max_chars]
    return text[: max_chars - len(suffix)] + suffix


def _bounded_list(values: Any, *, limit: int = _MAX_TAGS) -> List[str]:
    if not isinstance(values, list):
        return []
    out: List[str] = []
    seen: set[str] = set()
    for value in values[:limit]:
        text = _bounded_text(value)
        if not text or not text.strip() or text in seen:
            continue
        seen.add(text)
        out.append(text)
    return out


def build_review_action_event(
    *,
    review_id: str,
    action_type: str,
    feedback_token: str | None = None,
    tenant_id: str = "default",
    selected_candidate_id: str | None = None,
    operator_note: str | None = None,
    status_after: str | None = None,
    tags: List[str] | None = None,
    action_id: str | None = None,
    created_at: str | None = None,
) -> Dict[str, Any]:
    return {
        "action_id": action_id or f"action_{uuid.uuid4().hex}",
        "review_id": _bounded_text(review_id),
        "feedback_token": _bounded_text(feedback_token),
        "tenant_id": _bounded_text(tenant_id or "default") or "default",
        "action_type": action_type,
        "selected_candidate_id": _bounded_text(selected_candidate_id),
        "operator_note": _bounded_text(operator_note),
        "status_after": status_after,
        "tags": _bounded_list(tags),
        "created_at": created_at or datetime.now(timezone.utc).isoformat(),
    }


def append_review_action_event(event: Dict[str, Any]) -> bool:
    try:
        path = Path(config.RUNS_DIR) / "review" / "review_actions.jsonl"
        payload = dict(event or {})
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(payload, ensure_ascii=False, default=str) + "\n")
        return True
    except Exception:
        return False
