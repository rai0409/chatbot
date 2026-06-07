from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Sequence, Tuple

import config
from rag_core.japanese_normalizer import (
    compute_business_term_overlap_score,
    detect_negative_mismatch,
    find_synonym_hits,
    load_japanese_business_synonyms,
)


DEFAULT_PROFILE_PATH = Path(config.BASE_DIR) / "configs" / "approved_similar_feature_rerank_profile.json"
_BASE_SCORE_KEYS = ("weighted_score", "final_score", "score", "similarity")
_MAX_REASONS = 8
_MAX_TERMS = 20
_SAFE_CANDIDATE_KEYS = {
    "qa_id",
    "question_text",
    "approved_answer_preview",
    "answer_text_preview",
    "scores",
    "score",
    "weighted_score",
    "final_score",
    "similarity",
    "decision_route",
    "matched_terms",
}
_DEFAULT_PROFILE = {
    "profile_name": "approved_similar_feature_preview",
    "profile_type": "approved_similar_feature_rerank",
    "runtime_enabled": False,
    "production_enabled": False,
    "weights": {
        "base_score": 1.0,
        "synonym_overlap": 0.08,
        "business_term_overlap": 0.10,
        "negative_mismatch_penalty": 0.18,
    },
    "limits": {
        "max_positive_adjustment": 0.12,
        "max_negative_penalty": 0.20,
    },
    "safety": {
        "no_runtime_ranking_change": True,
        "no_auto_answer_enablement": True,
        "requires_offline_evaluation_before_production": True,
    },
}


def _float_or_none(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _bounded_list(values: Sequence[Any], limit: int = _MAX_TERMS) -> List[str]:
    out: List[str] = []
    seen = set()
    for value in values:
        text = str(value or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        out.append(text)
        if len(out) >= limit:
            break
    return out


def _base_score(candidate: Dict[str, Any]) -> float | None:
    scores = candidate.get("scores")
    if isinstance(scores, dict):
        for key in _BASE_SCORE_KEYS:
            score = _float_or_none(scores.get(key))
            if score is not None:
                return score
    for key in _BASE_SCORE_KEYS:
        score = _float_or_none(candidate.get(key))
        if score is not None:
            return score
    return None


def _candidate_feature_text(candidate: Dict[str, Any]) -> str:
    parts = [
        candidate.get("question_text"),
        candidate.get("approved_answer_preview"),
        candidate.get("answer_text_preview"),
    ]
    return " ".join(str(part) for part in parts if part)


def _safe_candidate_copy(candidate: Dict[str, Any]) -> Dict[str, Any]:
    return {
        key: candidate.get(key)
        for key in _SAFE_CANDIDATE_KEYS
        if key in candidate
    }


def _valid_profile(profile: Dict[str, Any]) -> Tuple[bool, str | None]:
    if profile.get("production_enabled") is True:
        return False, "production_enabled_must_be_false"
    if profile.get("profile_name") is not None and profile.get("profile_name") != "approved_similar_feature_preview":
        return False, "invalid_profile_name"
    if profile.get("profile_type") is not None and profile.get("profile_type") != "approved_similar_feature_rerank":
        return False, "invalid_profile_type"
    safety = profile.get("safety")
    if isinstance(safety, dict):
        if safety.get("no_runtime_ranking_change") is not None and safety.get("no_runtime_ranking_change") is not True:
            return False, "unsafe_runtime_flag"
        if safety.get("no_auto_answer_enablement") is not None and safety.get("no_auto_answer_enablement") is not True:
            return False, "unsafe_auto_answer_flag"
        if safety.get("requires_offline_evaluation_before_production") is not None and safety.get("requires_offline_evaluation_before_production") is not True:
            return False, "offline_evaluation_flag_missing"
    return True, None


def load_feature_rerank_profile(path: str | Path | None = None) -> Dict[str, Any]:
    profile_path = Path(path) if path is not None else DEFAULT_PROFILE_PATH
    if not profile_path.exists():
        profile = dict(_DEFAULT_PROFILE)
        profile["_metadata"] = {"loaded": False, "path": str(profile_path), "reason": "missing"}
        return profile
    try:
        payload = json.loads(profile_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid approved_similar feature rerank profile: {profile_path}") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"approved_similar feature rerank profile must be a JSON object: {profile_path}")
    profile = dict(_DEFAULT_PROFILE)
    profile.update(payload)
    profile["_metadata"] = {"loaded": True, "path": str(profile_path), "reason": None}
    return profile


def _weights(profile: Dict[str, Any]) -> Dict[str, float]:
    raw = profile.get("weights") if isinstance(profile.get("weights"), dict) else {}
    defaults = _DEFAULT_PROFILE["weights"]
    return {
        key: float(raw.get(key, defaults[key]))
        for key in defaults
    }


def _limits(profile: Dict[str, Any]) -> Dict[str, float]:
    raw = profile.get("limits") if isinstance(profile.get("limits"), dict) else {}
    defaults = _DEFAULT_PROFILE["limits"]
    return {
        key: float(raw.get(key, defaults[key]))
        for key in defaults
    }


def _score_candidate(
    *,
    query: str,
    candidate: Dict[str, Any],
    profile: Dict[str, Any],
    synonym_config: Dict[str, Any],
) -> Dict[str, Any]:
    weights = _weights(profile)
    limits = _limits(profile)
    text = _candidate_feature_text(candidate)
    base = _base_score(candidate)
    synonym_hits = find_synonym_hits(query, text, synonym_config)
    synonym_score = float(synonym_hits.get("score") or 0.0)
    business_score = float(compute_business_term_overlap_score(query, text, synonym_config))
    mismatch = detect_negative_mismatch(query, text, synonym_config)

    positive = (
        weights["synonym_overlap"] * synonym_score
        + weights["business_term_overlap"] * business_score
    )
    positive = min(float(limits["max_positive_adjustment"]), positive)
    penalty = 0.0
    reasons: List[str] = []
    if synonym_score > 0:
        reasons.append("synonym_overlap")
    if business_score > 0:
        reasons.append("business_term_overlap")
    if mismatch.get("matched"):
        penalty = min(float(limits["max_negative_penalty"]), float(weights["negative_mismatch_penalty"]))
        reasons.append("negative_mismatch_penalty")

    adjustment = round(positive - penalty, 6)
    adjusted = round(base * weights["base_score"] + adjustment, 6) if base is not None else None
    out = _safe_candidate_copy(candidate)
    out.update(
        {
            "feature_rerank_applied": True,
            "feature_base_score": base,
            "feature_adjusted_score": adjusted,
            "feature_score_adjustment": adjustment,
            "feature_synonym_overlap_score": synonym_score,
            "feature_business_term_overlap_score": business_score,
            "feature_negative_mismatch": bool(mismatch.get("matched")),
            "feature_rerank_reasons": _bounded_list(reasons, limit=_MAX_REASONS),
            "feature_matched_canonicals": _bounded_list(synonym_hits.get("shared_canonicals") or []),
            "feature_negative_mismatch_reason": mismatch.get("reason") if mismatch.get("matched") else None,
        }
    )
    return out


def _summary(
    *,
    candidates: Sequence[Dict[str, Any]],
    original_ids: Sequence[Any],
    profile: Dict[str, Any],
    valid: bool,
    reason: str | None,
) -> Dict[str, Any]:
    adjusted_count = sum(
        1 for candidate in candidates if candidate.get("feature_score_adjustment") not in {None, 0, 0.0}
    )
    return {
        "feature_rerank_applied": valid,
        "candidate_count": len(candidates),
        "adjusted_candidate_count": adjusted_count,
        "negative_mismatch_count": sum(1 for candidate in candidates if candidate.get("feature_negative_mismatch")),
        "reordered": [candidate.get("qa_id") for candidate in candidates] != list(original_ids),
        "profile_name": profile.get("profile_name"),
        "production_enabled": profile.get("production_enabled"),
        "runtime_enabled": profile.get("runtime_enabled"),
        "valid_profile": valid,
        "profile_invalid_reason": reason,
    }


def apply_feature_rerank(
    query: str,
    candidates: Sequence[Dict[str, Any]],
    profile: Dict[str, Any] | None = None,
    synonym_config: Dict[str, Any] | None = None,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    active_profile = profile if profile is not None else load_feature_rerank_profile()
    valid, reason = _valid_profile(active_profile)
    copied = [
        _safe_candidate_copy(candidate)
        for candidate in candidates
        if isinstance(candidate, dict)
    ]
    original_ids = [candidate.get("qa_id") for candidate in copied]
    if not valid:
        return copied, _summary(
            candidates=copied,
            original_ids=original_ids,
            profile=active_profile,
            valid=False,
            reason=reason,
        )

    try:
        active_synonyms = synonym_config if synonym_config is not None else load_japanese_business_synonyms()
    except ValueError:
        active_synonyms = {"synonym_groups": [], "negative_mismatch_pairs": []}

    ranked = [
        (
            index,
            _score_candidate(
                query=query,
                candidate=candidate,
                profile=active_profile,
                synonym_config=active_synonyms,
            ),
        )
        for index, candidate in enumerate(copied)
    ]
    scored = [candidate for _, candidate in ranked]
    sortable = bool(scored) and all(candidate.get("feature_base_score") is not None for candidate in scored)
    if sortable:
        ranked.sort(
            key=lambda item: (
                float(item[1].get("feature_adjusted_score") or 0.0),
                -item[0],
            ),
            reverse=True,
        )
        scored = [candidate for _, candidate in ranked]
    return scored, _summary(
        candidates=scored,
        original_ids=original_ids,
        profile=active_profile,
        valid=True,
        reason=None,
    )
