"""Turn thumbs-down feedback into counted observations and, on request, into "observed" proposals."""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from eval.tenant_eval import proposals
from eval.tenant_eval.stats import clopper_pearson_lower, clopper_pearson_upper

# The reasons of the chat UI, and where to look first (the first category is the primary suggestion; a person decides).
REASON_CATEGORIES: Dict[str, Tuple[str, ...]] = {
    "wrong_intent": ("synonym", "boost_rule"),
    "incorrect": ("validation_rule", "boost_rule"),
    "outdated": ("stale_content",),
    "wrong_source": ("boost_rule", "retrieval_setting"),
    "incomplete": ("ingest_gap", "retrieval_setting"),
}
TOKEN_LIMIT = 5
_SAFE = re.compile(r"[^A-Za-z0-9_-]+")


def categories_for(reason: Any) -> Tuple[str, ...]:
    return REASON_CATEGORIES.get(str(reason or "").strip(), ("other",))


def latest_by_token(events: Sequence[Mapping[str, Any]]) -> List[Mapping[str, Any]]:
    """One vote per feedback token (the latest event); events without a token each count."""
    latest: Dict[str, Mapping[str, Any]] = {}
    for index, event in enumerate(events):
        token = str(event.get("feedback_token") or "").strip()
        latest[token or f"\0{index}"] = event
    return list(latest.values())


def aggregate(
    events: Sequence[Mapping[str, Any]], *, min_count: int = 5, alpha: float = 0.05
) -> Dict[str, Any]:
    """Per tenant: votes, the bad rate with a two-sided (1 - alpha) interval, reasons, categories; plus observations."""
    tenants: Dict[str, Dict[str, Any]] = {}
    tokens: Dict[Tuple[str, str], List[str]] = {}
    for event in latest_by_token(events):
        kind = str(event.get("feedback_type") or "").strip()
        if kind not in {"good", "bad"}:
            continue
        tenant = str(event.get("tenant_id") or "default")
        row = tenants.setdefault(tenant, {"good": 0, "bad": 0, "reasons": {}, "categories": {}})
        row[kind] += 1
        if kind == "bad":
            reason = str(event.get("bad_reason") or "other").strip() or "other"
            row["reasons"][reason] = row["reasons"].get(reason, 0) + 1
            category = categories_for(reason)[0]
            row["categories"][category] = row["categories"].get(category, 0) + 1
            token = str(event.get("feedback_token") or "").strip()
            if token:
                tokens.setdefault((tenant, reason), []).append(token)
    observations: List[Dict[str, Any]] = []
    for tenant, row in sorted(tenants.items()):
        total = row["good"] + row["bad"]
        row["total"] = total
        row["bad_rate"] = row["bad"] / total if total else None
        row["bad_rate_low"] = clopper_pearson_lower(row["bad"], total, alpha / 2) if total else None
        row["bad_rate_high"] = clopper_pearson_upper(row["bad"], total, alpha / 2) if total else None
        for reason, count in row["reasons"].items():
            if count >= min_count:
                observations.append({
                    "tenant_id": tenant,
                    "reason": reason,
                    "primary_category": categories_for(reason)[0],
                    "candidate_categories": list(categories_for(reason)),
                    "count": count,
                    "share_of_bad": count / row["bad"],
                    "feedback_tokens": tokens.get((tenant, reason), [])[:TOKEN_LIMIT],
                })
    observations.sort(key=lambda item: (-item["count"], item["tenant_id"], item["reason"]))
    return {"tenants": tenants, "observations": observations}


def observation_id(observation: Mapping[str, Any], day: str) -> str:
    return _SAFE.sub("-", f"fb-{observation['tenant_id']}-{observation['reason']}-{day}")[:64]


def open_observations(
    observations: Sequence[Mapping[str, Any]], *, profile_id: str, actor: str, now: Optional[datetime] = None
) -> List[str]:
    """Create an "observed" proposal for each observation (once per tenant, reason and day). Returns the new ids."""
    moment = now or datetime.now(timezone.utc)
    day = moment.strftime("%Y%m%d")
    created: List[str] = []
    for observation in observations:
        proposal_id = observation_id(observation, day)
        try:
            proposals.create(
                proposal_id,
                profile_id=profile_id,
                title=f"フィードバック「{observation['reason']}」が {observation['count']} 件（{observation['tenant_id']}）",
                actor=actor,
                source={
                    "kind": "feedback",
                    "tenant_id": observation["tenant_id"],
                    "reason": observation["reason"],
                    "count": observation["count"],
                    "suggested_categories": list(observation["candidate_categories"]),
                    "feedback_tokens": list(observation["feedback_tokens"]),
                },
                now=moment,
            )
        except proposals.ProposalError as exc:
            if "already exists" in str(exc):
                continue
            raise
        created.append(proposal_id)
    return created
