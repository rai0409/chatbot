from __future__ import annotations

import json
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List, Sequence

import config
from rag_core import embedder, store
from rag_core.ja_text import extract_salient_terms_ja, normalize_japanese_text


_MAX_TERMS = 16
_MAX_FIELDS = 10
_MAX_WEIGHT_DETAILS = 32
_DEFAULT_DECISION_THRESHOLDS = {
    "high_confidence_score": 0.82,
    "high_confidence_margin": 0.08,
    "low_confidence_score": 0.45,
}
_NEGATION_TERMS = ("ない", "不要", "不可", "できない", "しない", "含まない", "含まれない", "含まれません", "対象外")
_GENERIC_MATCH_TERMS = {
    "含む",
    "含まれる",
    "含みます",
    "含まれます",
    "入る",
    "入ります",
    "対象",
    "設問",
    "項目",
    "質問",
    "程度",
    "認識",
    "良い",
    "でしょう",
    "ですか",
    "ますか",
}
_SYNONYM_GROUPS = (
    ("自由回答", "フリーアンサー", "自由記述"),
    ("設問", "項目", "質問"),
    ("含む", "含まれる", "含みます", "含まれます", "入る", "入ります", "対象"),
    ("アンケート", "調査", "質問票"),
    ("アンケートシステム", "アンケートフォーム"),
)
_BASE_FIELD_NAMES = ("question_text", "answer_text", "normalized_question", "title", "section_path", "text")
_PROFILE_FIELD_NAMES = {
    "question_text",
    "answer_text",
    "approved_answer",
    "approved_answer_preview",
    "normalized_question",
    "title",
    "section_path",
    "searchable_text",
    "text",
}


def _build_synonym_map() -> Dict[str, List[str]]:
    out: Dict[str, List[str]] = {}
    for group in _SYNONYM_GROUPS:
        normalized = [_norm(item) for item in group if _norm(item)]
        for item in normalized:
            out[item] = [other for other in normalized if other != item]
    return out


_SYNONYMS: Dict[str, List[str]]


@dataclass(frozen=True)
class ApprovedSimilarCandidate:
    qa_id: str
    question_text: str
    answer_preview: str
    semantic_score: float | None
    semantic_distance: float | None
    keyword_score: float
    hybrid_score: float | None
    top1_top2_margin: float | None
    margin_score_basis: str | None
    matched_terms: List[str]
    matched_fields: List[str]
    source_doc: str
    source_pages: Any
    doc_version: str
    tenant_id: str
    chunk_type: str
    doc_type: str
    numeric_conflict: bool
    negation_conflict: bool
    synonym_matches: List[Dict[str, str]]
    generic_matched_terms: List[str]
    specific_matched_terms: List[str]
    keyword_weight_details: List[Dict[str, Any]]
    field_weight_details: List[Dict[str, Any]]
    weighted_keyword_score: float

    def to_dict(self) -> Dict[str, Any]:
        return {
            "qa_id": self.qa_id,
            "question_text": self.question_text,
            "approved_answer_preview": self.answer_preview,
            "answer_text_preview": self.answer_preview,
            "semantic_score": self.semantic_score,
            "semantic_distance": self.semantic_distance,
            "keyword_score": self.keyword_score,
            "hybrid_score": self.hybrid_score,
            "top1_top2_margin": self.top1_top2_margin,
            "margin_score_basis": self.margin_score_basis,
            "matched_terms": self.matched_terms,
            "matched_fields": self.matched_fields,
            "source_doc": self.source_doc,
            "source_pages": self.source_pages,
            "doc_version": self.doc_version,
            "tenant_id": self.tenant_id,
            "chunk_type": self.chunk_type,
            "doc_type": self.doc_type,
            "numeric_conflict": self.numeric_conflict,
            "negation_conflict": self.negation_conflict,
            "synonym_matches": self.synonym_matches,
            "generic_matched_terms": self.generic_matched_terms,
            "specific_matched_terms": self.specific_matched_terms,
            "keyword_weight_details": self.keyword_weight_details,
            "field_weight_details": self.field_weight_details,
            "weighted_keyword_score": self.weighted_keyword_score,
        }


def _norm(text: Any) -> str:
    return normalize_japanese_text(str(text or "")).lower()


_SYNONYMS = _build_synonym_map()


def _compact(text: Any) -> str:
    return re.sub(r"\s+", "", _norm(text))


def _unique(items: Sequence[str], *, limit: int | None = None) -> List[str]:
    out: List[str] = []
    seen = set()
    for raw in items:
        item = _norm(raw).strip()
        if not item or item in seen:
            continue
        seen.add(item)
        out.append(item)
        if limit is not None and len(out) >= limit:
            break
    return out


def _field_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        raw = value.strip()
        if raw and raw[0] in "[{":
            try:
                return _field_text(json.loads(raw))
            except Exception:
                return _norm(raw)
        return _norm(raw)
    if isinstance(value, dict):
        return _norm(" ".join(str(v) for v in value.values() if str(v).strip()))
    if isinstance(value, (list, tuple, set)):
        return _norm(" ".join(str(v) for v in value if str(v).strip()))
    return _norm(value)


def _configured_keyword_profile_path() -> str | None:
    raw = config.getenv_first("APPROVED_SIMILAR_KEYWORD_WEIGHTS", default=None)
    if raw is None or str(raw).strip() == "":
        return None
    path = Path(str(raw).strip())
    if not path.is_absolute():
        path = config.BASE_DIR / path
    return str(path)


@lru_cache(maxsize=8)
def _load_keyword_weight_profile(path: str | None) -> Dict[str, Any] | None:
    if not path:
        return None
    with Path(path).open("r", encoding="utf-8") as f:
        raw = json.load(f)
    if not isinstance(raw, dict):
        raise ValueError(f"approved similar keyword weight profile must be a JSON object: {path}")

    generic_terms = raw.get("generic_terms", raw.get("weak_terms", [])) or []
    specific_terms = raw.get("specific_terms", raw.get("strong_terms", [])) or []
    field_weights = raw.get("field_weights") or {}
    per_term_weights = raw.get("per_term_weights") or {}
    return {
        "path": path,
        "generic_terms": {_norm(term) for term in generic_terms if _norm(term)},
        "specific_terms": {_norm(term) for term in specific_terms if _norm(term)},
        "generic_multiplier": float(raw.get("generic_multiplier", raw.get("weak_multiplier", 1.0))),
        "specific_multiplier": float(raw.get("specific_multiplier", raw.get("strong_multiplier", 1.0))),
        "field_weights": {
            str(field): float(weight)
            for field, weight in dict(field_weights).items()
            if str(field) in _PROFILE_FIELD_NAMES
        },
        "per_term_weights": {
            _norm(term): float(weight)
            for term, weight in dict(per_term_weights).items()
            if _norm(term)
        },
    }


def _keyword_weight_profile() -> Dict[str, Any] | None:
    return _load_keyword_weight_profile(_configured_keyword_profile_path())


def _jsonish(value: Any) -> Any:
    if isinstance(value, str):
        raw = value.strip()
        if raw and raw[0] in "[{":
            try:
                return json.loads(raw)
            except Exception:
                return value
    return value


def _terms(text: str) -> List[str]:
    norm = _norm(text)
    terms: List[str] = []
    terms.extend(extract_salient_terms_ja(norm))
    terms.extend(re.findall(r"\d+(?:\.\d+)?", norm))
    terms.extend(re.findall(r"[a-z0-9][a-z0-9._:/-]{1,}", norm))
    terms.extend(re.findall(r"[ァ-ヴー]{2,}", norm))
    terms.extend(re.findall(r"[一-龥々〆〤]{2,}", norm))
    expanded: List[str] = []
    for term in terms:
        expanded.append(term)
        expanded.extend(_SYNONYMS.get(_norm(term), []))
    return _unique(expanded, limit=32)


def _base_terms(text: str) -> List[str]:
    norm = _norm(text)
    terms: List[str] = []
    terms.extend(extract_salient_terms_ja(norm))
    terms.extend(re.findall(r"\d+(?:\.\d+)?", norm))
    terms.extend(re.findall(r"[a-z0-9][a-z0-9._:/-]{1,}", norm))
    terms.extend(re.findall(r"[ァ-ヴー]{2,}", norm))
    terms.extend(re.findall(r"[一-龥々〆〤]{2,}", norm))
    return _unique(terms, limit=32)


def _contains(field: str, term: str) -> bool:
    return bool(term) and (_norm(term) in field or _compact(term) in _compact(field))


def _is_number_term(term: str) -> bool:
    return bool(re.fullmatch(r"\d+(?:\.\d+)?", _norm(term)))


def _is_topic_term(term: str) -> bool:
    value = _norm(term)
    compact = _compact(value)
    if not compact or _is_number_term(value) or value in _GENERIC_MATCH_TERMS:
        return False
    return len(compact) >= 2


def _term_weight_class(term: str, profile: Dict[str, Any] | None) -> str:
    value = _norm(term)
    if profile is not None:
        if value in profile["specific_terms"]:
            return "specific"
        if value in profile["generic_terms"]:
            return "generic"
    if value in _GENERIC_MATCH_TERMS:
        return "generic"
    if _is_topic_term(value):
        return "specific"
    return "generic"


def _profiled_field_names(profile: Dict[str, Any] | None) -> tuple[str, ...]:
    if profile is None:
        return _BASE_FIELD_NAMES
    extra = tuple(field for field in profile["field_weights"] if field not in _BASE_FIELD_NAMES)
    return _BASE_FIELD_NAMES + extra


def _field_match_weight(term: str, field_name: str) -> float:
    if _is_number_term(term):
        return 0.0
    if _is_topic_term(term):
        if field_name in {"question_text", "normalized_question"}:
            return 0.26
        if field_name == "answer_text":
            return 0.13
        if field_name in {"title", "section_path"}:
            return 0.08
        return 0.04
    if field_name in {"question_text", "normalized_question"}:
        return 0.08
    if field_name == "answer_text":
        return 0.04
    if field_name in {"title", "section_path"}:
        return 0.03
    return 0.02


def _weighted_match(
    *,
    term: str,
    field_name: str,
    base_weight: float,
    profile: Dict[str, Any] | None,
    reason: str,
) -> tuple[float, Dict[str, Any]]:
    term_norm = _norm(term)
    field_multiplier = 1.0
    class_multiplier = 1.0
    per_term_multiplier = 1.0
    term_class = _term_weight_class(term_norm, profile)
    if profile is not None:
        field_multiplier = float(profile["field_weights"].get(field_name, 1.0))
        per_term_multiplier = float(profile["per_term_weights"].get(term_norm, 1.0))
        if term_class == "specific":
            class_multiplier = float(profile["specific_multiplier"])
        elif term_class == "generic":
            class_multiplier = float(profile["generic_multiplier"])
    weighted = base_weight * field_multiplier * class_multiplier * per_term_multiplier
    return weighted, {
        "term": term_norm,
        "term_class": term_class,
        "field": field_name,
        "reason": reason,
        "base_weight": round(base_weight, 6),
        "field_multiplier": round(field_multiplier, 6),
        "class_multiplier": round(class_multiplier, 6),
        "per_term_multiplier": round(per_term_multiplier, 6),
        "weighted": round(weighted, 6),
    }


def _append_match_evidence(
    *,
    term: str,
    field_name: str,
    matched_terms: List[str],
    matched_fields: List[str],
    generic_matched_terms: List[str],
    specific_matched_terms: List[str],
    profile: Dict[str, Any] | None,
) -> None:
    term_norm = _norm(term)
    if term_norm not in matched_terms and len(matched_terms) < _MAX_TERMS:
        matched_terms.append(term_norm)
    if field_name not in matched_fields and len(matched_fields) < _MAX_FIELDS:
        matched_fields.append(field_name)
    term_class = _term_weight_class(term_norm, profile)
    if term_class == "specific":
        if term_norm not in specific_matched_terms and len(specific_matched_terms) < _MAX_TERMS:
            specific_matched_terms.append(term_norm)
    elif term_norm not in generic_matched_terms and len(generic_matched_terms) < _MAX_TERMS:
        generic_matched_terms.append(term_norm)


def _field_weight_details(keyword_weight_details: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    by_field: Dict[str, Dict[str, Any]] = {}
    for detail in keyword_weight_details:
        field = str(detail.get("field") or "")
        if not field:
            continue
        item = by_field.setdefault(
            field,
            {
                "field": field,
                "match_count": 0,
                "weighted_total": 0.0,
                "base_total": 0.0,
            },
        )
        item["match_count"] += 1
        item["weighted_total"] += float(detail.get("weighted") or 0.0)
        item["base_total"] += float(detail.get("base_weight") or 0.0)
    return [
        {
            **item,
            "weighted_total": round(float(item["weighted_total"]), 6),
            "base_total": round(float(item["base_total"]), 6),
        }
        for item in by_field.values()
    ][: _MAX_FIELDS]


def _numbers(text: str) -> set[str]:
    return set(re.findall(r"\d+(?:\.\d+)?", _norm(text)))


def _has_negation(text: str) -> bool:
    norm = _norm(text)
    return any(term in norm for term in _NEGATION_TERMS)


def score_approved_candidate_keyword(query: str, metadata: dict, text: str = "") -> Dict[str, Any]:
    profile = _keyword_weight_profile()
    fields = {
        "question_text": _field_text(metadata.get("question_text")),
        "answer_text": _field_text(metadata.get("answer_text") or metadata.get("approved_answer")),
        "approved_answer": _field_text(metadata.get("approved_answer")),
        "approved_answer_preview": _field_text(metadata.get("approved_answer_preview")),
        "normalized_question": _field_text(metadata.get("normalized_question")),
        "title": _field_text(metadata.get("title")),
        "section_path": _field_text(metadata.get("section_path")),
        "searchable_text": _field_text(metadata.get("searchable_text") or text),
        "text": _field_text(text),
    }
    scored_field_names = _profiled_field_names(profile)
    query_terms = _terms(query)
    query_base_terms = _base_terms(query)
    matched_terms: List[str] = []
    matched_fields: List[str] = []
    generic_matched_terms: List[str] = []
    specific_matched_terms: List[str] = []
    keyword_weight_details: List[Dict[str, Any]] = []
    synonym_matches: List[Dict[str, str]] = []
    score = 0.0
    for term in query_terms:
        if _is_number_term(term):
            continue
        for field_name in scored_field_names:
            field_value = fields.get(field_name, "")
            if not _contains(field_value, term):
                continue
            _append_match_evidence(
                term=term,
                field_name=field_name,
                matched_terms=matched_terms,
                matched_fields=matched_fields,
                generic_matched_terms=generic_matched_terms,
                specific_matched_terms=specific_matched_terms,
                profile=profile,
            )
            weighted, detail = _weighted_match(
                term=term,
                field_name=field_name,
                base_weight=_field_match_weight(term, field_name),
                profile=profile,
                reason="term",
            )
            score += weighted
            if len(keyword_weight_details) < _MAX_WEIGHT_DETAILS:
                keyword_weight_details.append(detail)
            break

    for query_term in query_base_terms:
        for synonym in _SYNONYMS.get(_norm(query_term), []):
            for field_name in tuple(field for field in scored_field_names if field in {"question_text", "normalized_question", "answer_text", "approved_answer", "approved_answer_preview", "searchable_text", "text"}):
                if not _contains(fields[field_name], synonym):
                    continue
                evidence = {
                    "query_term": _norm(query_term),
                    "matched_synonym": _norm(synonym),
                    "field": field_name,
                }
                if evidence not in synonym_matches:
                    synonym_matches.append(evidence)
                _append_match_evidence(
                    term=query_term,
                    field_name=field_name,
                    matched_terms=matched_terms,
                    matched_fields=matched_fields,
                    generic_matched_terms=generic_matched_terms,
                    specific_matched_terms=specific_matched_terms,
                    profile=profile,
                )
                _append_match_evidence(
                    term=synonym,
                    field_name=field_name,
                    matched_terms=matched_terms,
                    matched_fields=matched_fields,
                    generic_matched_terms=generic_matched_terms,
                    specific_matched_terms=specific_matched_terms,
                    profile=profile,
                )
                weighted, detail = _weighted_match(
                    term=synonym,
                    field_name=field_name,
                    base_weight=_field_match_weight(synonym, field_name) * 0.7,
                    profile=profile,
                    reason="synonym",
                )
                score += weighted
                if len(keyword_weight_details) < _MAX_WEIGHT_DETAILS:
                    keyword_weight_details.append(detail)
                break

    q_nums = _numbers(query)
    candidate_nums = _numbers(" ".join(fields.values()))
    numeric_overlap = bool(q_nums and q_nums & candidate_nums)
    numeric_conflict = bool(q_nums and candidate_nums and not (q_nums & candidate_nums))
    if numeric_overlap:
        score += 0.08
        for num in sorted(q_nums & candidate_nums):
            if num not in matched_terms and len(matched_terms) < _MAX_TERMS:
                matched_terms.append(num)
        if "numeric" not in matched_fields and len(matched_fields) < _MAX_FIELDS:
            matched_fields.append("numeric")
        if len(keyword_weight_details) < _MAX_WEIGHT_DETAILS:
            keyword_weight_details.append(
                {
                    "term": ",".join(sorted(q_nums & candidate_nums)),
                    "term_class": "numeric",
                    "field": "numeric",
                    "reason": "numeric_overlap",
                    "base_weight": 0.08,
                    "field_multiplier": 1.0,
                    "class_multiplier": 1.0,
                    "per_term_multiplier": 1.0,
                    "weighted": 0.08,
                }
            )

    query_neg = _has_negation(query)
    candidate_neg = _has_negation(fields["question_text"] + " " + fields["answer_text"])
    negation_conflict = query_neg != candidate_neg and (query_neg or candidate_neg)
    if negation_conflict:
        score = max(0.0, score - 0.04)

    weighted_keyword_score = round(min(score, 1.0), 4)
    return {
        "keyword_score": weighted_keyword_score,
        "weighted_keyword_score": weighted_keyword_score,
        "matched_terms": matched_terms,
        "matched_fields": matched_fields,
        "generic_matched_terms": generic_matched_terms,
        "specific_matched_terms": specific_matched_terms,
        "keyword_weight_details": keyword_weight_details,
        "field_weight_details": _field_weight_details(keyword_weight_details),
        "synonym_matches": synonym_matches[:_MAX_TERMS],
        "numeric_conflict": numeric_conflict,
        "negation_conflict": negation_conflict,
    }


def _semantic_score(distance: float | None) -> float | None:
    if distance is None:
        return None
    return round(1.0 / (1.0 + max(0.0, float(distance))), 6)


def _preview(text: Any, max_chars: int = 220) -> str:
    value = str(text or "")
    if len(value) <= max_chars:
        return value
    return value[: max_chars - 14].rstrip() + "...[truncated]"


def _first_present(mapping: Dict[str, Any], keys: Sequence[str]) -> Any:
    for key in keys:
        value = mapping.get(key)
        if value is not None and str(value).strip() != "":
            return value
    return None


def _float_or_none(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _confidence_like_score(candidate: Dict[str, Any]) -> float | None:
    return _float_or_none(
        _first_present(
            candidate,
            (
                "hybrid_score",
                "weighted_keyword_score",
                "keyword_score",
                "semantic_score",
            ),
        )
    )


def _score_snapshot(candidate: Dict[str, Any]) -> Dict[str, float | None]:
    return {
        "hybrid_score": _float_or_none(candidate.get("hybrid_score")),
        "semantic_score": _float_or_none(candidate.get("semantic_score")),
        "keyword_score": _float_or_none(candidate.get("keyword_score")),
        "weighted_keyword_score": _float_or_none(candidate.get("weighted_keyword_score")),
        "top1_top2_margin": _float_or_none(candidate.get("top1_top2_margin")),
    }


def _top_candidate_summary(candidate: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "qa_id": candidate.get("qa_id"),
        "question_text": candidate.get("question_text"),
        "answer_preview": _first_present(
            candidate,
            (
                "approved_answer_preview",
                "answer_text_preview",
                "approved_answer",
                "answer_text",
            ),
        ),
        "generic_matched_terms": list(candidate.get("generic_matched_terms") or [])[:_MAX_TERMS],
        "specific_matched_terms": list(candidate.get("specific_matched_terms") or [])[:_MAX_TERMS],
        "matched_terms": list(candidate.get("matched_terms") or [])[:_MAX_TERMS],
        "matched_fields": list(candidate.get("matched_fields") or [])[:_MAX_FIELDS],
    }


def decide_approved_similar_candidate(
    candidates: Sequence[Dict[str, Any]],
    *,
    thresholds: Dict[str, float] | None = None,
) -> Dict[str, Any]:
    effective_thresholds = dict(_DEFAULT_DECISION_THRESHOLDS)
    if thresholds:
        effective_thresholds.update({str(key): float(value) for key, value in thresholds.items()})

    if not candidates:
        return {
            "route": "no_candidate",
            "qa_id": None,
            "confidence_like_score": None,
            "reasons": ["no approved_similar_candidate candidates"],
            "blocking_flags": {
                "numeric_conflict": False,
                "negation_conflict": False,
                "ambiguous": False,
            },
            "score_snapshot": {},
            "top_candidate_summary": None,
            "thresholds": effective_thresholds,
        }

    top = dict(candidates[0] or {})
    qa_id = top.get("qa_id")
    score = _confidence_like_score(top)
    margin = _float_or_none(top.get("top1_top2_margin"))
    numeric_conflict = bool(top.get("numeric_conflict", False))
    negation_conflict = bool(top.get("negation_conflict", False))
    ambiguous = bool(top.get("ambiguous", False))
    blocking_flags = {
        "numeric_conflict": numeric_conflict,
        "negation_conflict": negation_conflict,
        "ambiguous": ambiguous,
    }
    score_snapshot = _score_snapshot(top)
    top_summary = _top_candidate_summary(top)

    route = "candidate_only"
    reasons: List[str] = []
    if numeric_conflict:
        route = "numeric_conflict_blocked"
        reasons.append("top candidate has numeric_conflict")
    elif negation_conflict:
        route = "negation_conflict_review"
        reasons.append("top candidate has negation_conflict")
    elif ambiguous:
        route = "ambiguous_multi_topic"
        reasons.append("top candidate is marked ambiguous")
    elif score is None:
        route = "low_confidence_no_answer"
        reasons.append("top candidate has no usable confidence-like score")
    elif score < effective_thresholds["low_confidence_score"]:
        route = "low_confidence_no_answer"
        reasons.append(
            f"confidence_like_score {score:.6f} below low_confidence_score "
            f"{effective_thresholds['low_confidence_score']:.6f}"
        )
    elif score >= effective_thresholds["high_confidence_score"] and (
        margin is not None and margin >= effective_thresholds["high_confidence_margin"]
    ):
        route = "high_confidence_answer"
        reasons.append(
            f"confidence_like_score {score:.6f} and margin {margin:.6f} meet high confidence thresholds"
        )
    else:
        if score < effective_thresholds["high_confidence_score"]:
            reasons.append(
                f"confidence_like_score {score:.6f} below high_confidence_score "
                f"{effective_thresholds['high_confidence_score']:.6f}"
            )
        if margin is None:
            reasons.append("top1_top2_margin unavailable")
        elif margin < effective_thresholds["high_confidence_margin"]:
            reasons.append(
                f"top1_top2_margin {margin:.6f} below high_confidence_margin "
                f"{effective_thresholds['high_confidence_margin']:.6f}"
            )

    return {
        "route": route,
        "qa_id": qa_id,
        "confidence_like_score": score,
        "reasons": reasons[:8],
        "blocking_flags": blocking_flags,
        "score_snapshot": score_snapshot,
        "top_candidate_summary": top_summary,
        "thresholds": effective_thresholds,
    }


def _qa_pair_where() -> Dict[str, Any]:
    return {
        "$and": [
            {"doc_type": "approved_qa_pair"},
            {"chunk_type": "qa_pair"},
            {"searchable": 1},
        ]
    }


def _query_collection(collection: Any, embedding: List[float], n_results: int) -> Dict[str, Any]:
    try:
        return collection.query(
            query_embeddings=[embedding],
            n_results=n_results,
            where=_qa_pair_where(),
            include=["documents", "metadatas", "distances"],
        )
    except Exception:
        try:
            return collection.query(
                query_embeddings=[embedding],
                n_results=n_results,
                where={"doc_type": "approved_qa_pair"},
                include=["documents", "metadatas", "distances"],
            )
        except Exception:
            return collection.query(
                query_embeddings=[embedding],
                n_results=n_results,
                include=["documents", "metadatas", "distances"],
            )


def _margin(candidates: Sequence[ApprovedSimilarCandidate]) -> tuple[float | None, str | None]:
    if len(candidates) < 2:
        return None, None
    if candidates[0].hybrid_score is not None and candidates[1].hybrid_score is not None:
        return round(candidates[0].hybrid_score - candidates[1].hybrid_score, 6), "hybrid_score"
    if candidates[0].semantic_score is not None and candidates[1].semantic_score is not None:
        return round(candidates[0].semantic_score - candidates[1].semantic_score, 6), "semantic_score"
    return None, None


def build_approved_similar_candidate(
    *,
    query: str,
    text: str,
    metadata: dict,
    distance: float | None,
) -> ApprovedSimilarCandidate:
    meta = dict(metadata or {})
    keyword = score_approved_candidate_keyword(query, meta, text=text)
    semantic = _semantic_score(distance)
    conflict_penalty = 0.08 if keyword["numeric_conflict"] else 0.0
    if keyword["negation_conflict"]:
        conflict_penalty += 0.02
    hybrid = None
    if semantic is not None:
        hybrid = round(max(0.0, semantic * 0.60 + float(keyword["keyword_score"]) * 0.40 - conflict_penalty), 6)
    return ApprovedSimilarCandidate(
        qa_id=str(meta.get("qa_id") or ""),
        question_text=str(meta.get("question_text") or ""),
        answer_preview=_preview(meta.get("answer_text") or meta.get("approved_answer") or ""),
        semantic_score=semantic,
        semantic_distance=None if distance is None else float(distance),
        keyword_score=float(keyword["keyword_score"]),
        hybrid_score=hybrid,
        top1_top2_margin=None,
        margin_score_basis=None,
        matched_terms=list(keyword["matched_terms"]),
        matched_fields=list(keyword["matched_fields"]),
        source_doc=str(meta.get("source_doc") or ""),
        source_pages=_jsonish(meta.get("source_pages")) or [],
        doc_version=str(meta.get("doc_version") or ""),
        tenant_id=str(meta.get("tenant_id") or "default"),
        chunk_type=str(meta.get("chunk_type") or ""),
        doc_type=str(meta.get("doc_type") or ""),
        numeric_conflict=bool(keyword["numeric_conflict"]),
        negation_conflict=bool(keyword["negation_conflict"]),
        synonym_matches=list(keyword.get("synonym_matches") or []),
        generic_matched_terms=list(keyword.get("generic_matched_terms") or []),
        specific_matched_terms=list(keyword.get("specific_matched_terms") or []),
        keyword_weight_details=list(keyword.get("keyword_weight_details") or []),
        field_weight_details=list(keyword.get("field_weight_details") or []),
        weighted_keyword_score=float(keyword.get("weighted_keyword_score", keyword["keyword_score"])),
    )


def search_approved_similar_candidates(
    query: str,
    *,
    client: Any = None,
    collection_name: str | None = None,
    top_k: int = 5,
    oversample: int = 4,
) -> List[Dict[str, Any]]:
    collection = client or store.get_vectorstore(collection_name=collection_name)
    embedding = embedder.embed_queries([query], client=client)[0]
    n_results = max(top_k, top_k * max(1, oversample))
    result = _query_collection(collection, embedding, n_results)
    docs = (result.get("documents") or [[]])[0]
    metas = (result.get("metadatas") or [[]])[0]
    distances = (result.get("distances") or [[]])[0]
    candidates: List[ApprovedSimilarCandidate] = []
    seen: set[str] = set()
    for text, meta, distance in zip(docs, metas, distances):
        m = dict(meta or {})
        if m.get("doc_type") != "approved_qa_pair" or m.get("chunk_type") != "qa_pair":
            continue
        qa_id = str(m.get("qa_id") or m.get("id") or "")
        if qa_id in seen:
            continue
        seen.add(qa_id)
        candidates.append(
            build_approved_similar_candidate(
                query=query,
                text=str(text or ""),
                metadata=m,
                distance=float(distance) if distance is not None else None,
            )
        )
    candidates.sort(
        key=lambda c: (
            c.hybrid_score if c.hybrid_score is not None else -1.0,
            c.semantic_score if c.semantic_score is not None else -1.0,
            c.keyword_score,
        ),
        reverse=True,
    )
    selected = candidates[:top_k]
    margin, basis = _margin(selected)
    return [
        ApprovedSimilarCandidate(
            **{
                **candidate.__dict__,
                "top1_top2_margin": margin,
                "margin_score_basis": basis,
            }
        ).to_dict()
        for candidate in selected
    ]
