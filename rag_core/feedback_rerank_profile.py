from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Sequence, Tuple

import config


KNOWN_PROFILE_NAME = "feedback_preview"
PROFILE_PATH = Path(config.BASE_DIR) / "configs" / "approved_similar_rerank_weights_feedback_preview.json"
_BASE_SCORE_KEYS = ("weighted_score", "final_score", "score", "similarity")
_FALLBACK_SCORE_KEYS = ("hybrid_score", "weighted_keyword_score", "semantic_score")


def _float_or_none(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _base_score(candidate: Dict[str, Any]) -> float | None:
    scores = candidate.get("scores")
    if isinstance(scores, dict):
        for key in _BASE_SCORE_KEYS:
            score = _float_or_none(scores.get(key))
            if score is not None:
                return score
    for key in _BASE_SCORE_KEYS + _FALLBACK_SCORE_KEYS:
        score = _float_or_none(candidate.get(key))
        if score is not None:
            return score
    return None


def _default_meta(*, apply_feedback_preview: bool, rerank_profile: str | None) -> Dict[str, Any]:
    return {
        "apply_feedback_preview": bool(apply_feedback_preview),
        "rerank_profile": rerank_profile,
        "feedback_preview_applied": False,
        "feedback_preview_profile_path": str(PROFILE_PATH),
        "feedback_preview_adjusted_candidate_count": 0,
        "feedback_preview_missing_profile": False,
        "feedback_preview_invalid_profile": False,
        "feedback_preview_safety_checked": False,
        "feedback_preview_reordered": False,
    }


def _load_profile() -> Dict[str, Any] | None:
    if not PROFILE_PATH.exists():
        return None
    with PROFILE_PATH.open("r", encoding="utf-8") as f:
        payload = json.load(f)
    return payload if isinstance(payload, dict) else None


def _valid_profile(profile: Dict[str, Any]) -> bool:
    safety = profile.get("safety")
    return (
        profile.get("production_enabled") is False
        and profile.get("profile_name") == KNOWN_PROFILE_NAME
        and profile.get("profile_type") == "approved_similar_feedback_rerank"
        and isinstance(profile.get("candidate_adjustments"), dict)
        and isinstance(safety, dict)
        and safety.get("no_runtime_ranking_change") is True
        and safety.get("no_auto_answer_enablement") is True
        and safety.get("requires_offline_evaluation_before_production") is True
    )


def apply_feedback_preview_rerank(
    candidates: Sequence[Dict[str, Any]],
    *,
    apply_feedback_preview: bool = False,
    rerank_profile: str | None = None,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any], List[str]]:
    meta = _default_meta(
        apply_feedback_preview=apply_feedback_preview,
        rerank_profile=rerank_profile,
    )
    warnings: List[str] = []
    copied = [dict(candidate or {}) for candidate in candidates]

    if not apply_feedback_preview:
        return copied, meta, warnings
    if rerank_profile != KNOWN_PROFILE_NAME:
        meta["feedback_preview_invalid_profile"] = bool(rerank_profile)
        warnings.append("feedback_preview_rerank_profile_ignored")
        return copied, meta, warnings

    try:
        profile = _load_profile()
    except Exception:
        meta["feedback_preview_invalid_profile"] = True
        warnings.append("feedback_preview_rerank_profile_invalid")
        return copied, meta, warnings

    if profile is None:
        meta["feedback_preview_missing_profile"] = True
        warnings.append("feedback_preview_rerank_profile_missing")
        return copied, meta, warnings

    meta["feedback_preview_safety_checked"] = True
    if not _valid_profile(profile):
        meta["feedback_preview_invalid_profile"] = True
        warnings.append("feedback_preview_rerank_profile_invalid")
        return copied, meta, warnings

    adjustments = profile.get("candidate_adjustments") or {}
    adjusted_count = 0
    sortable = True
    ranked: List[Tuple[int, float | None, Dict[str, Any]]] = []
    for index, candidate in enumerate(copied):
        qa_id = str(candidate.get("qa_id") or "")
        adjustment = adjustments.get(qa_id)
        score = _base_score(candidate)
        if score is None:
            sortable = False
        if isinstance(adjustment, dict):
            amount = _float_or_none(adjustment.get("score_adjustment")) or 0.0
            candidate["feedback_preview_score_adjustment"] = amount
            candidate["feedback_preview_reasons"] = list(adjustment.get("reasons") or [])[:8]
            candidate["feedback_preview_positive_count"] = int(adjustment.get("positive_count") or 0)
            candidate["feedback_preview_negative_count"] = int(adjustment.get("negative_count") or 0)
            candidate["feedback_preview_review_needed_count"] = int(adjustment.get("review_needed_count") or 0)
            if score is not None:
                candidate["feedback_preview_adjusted_score"] = round(score + amount, 6)
            adjusted_count += 1
        ranked.append((index, score, candidate))

    meta["feedback_preview_applied"] = True
    meta["feedback_preview_adjusted_candidate_count"] = adjusted_count
    warnings.append("feedback_preview_rerank_applied_preview_only")

    if sortable and copied:
        original_ids = [candidate.get("qa_id") for candidate in copied]
        ranked.sort(
            key=lambda item: (
                _float_or_none(item[2].get("feedback_preview_adjusted_score"))
                if item[2].get("feedback_preview_adjusted_score") is not None
                else item[1],
                -item[0],
            ),
            reverse=True,
        )
        copied = [item[2] for item in ranked]
        meta["feedback_preview_reordered"] = [candidate.get("qa_id") for candidate in copied] != original_ids

    return copied, meta, warnings
