from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Sequence

from rag_core import embedder, store
from rag_core.ja_text import extract_salient_terms_ja, normalize_japanese_text


_MAX_TERMS = 16
_MAX_FIELDS = 10
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


def _numbers(text: str) -> set[str]:
    return set(re.findall(r"\d+(?:\.\d+)?", _norm(text)))


def _has_negation(text: str) -> bool:
    norm = _norm(text)
    return any(term in norm for term in _NEGATION_TERMS)


def score_approved_candidate_keyword(query: str, metadata: dict, text: str = "") -> Dict[str, Any]:
    fields = {
        "question_text": _field_text(metadata.get("question_text")),
        "answer_text": _field_text(metadata.get("answer_text") or metadata.get("approved_answer")),
        "normalized_question": _field_text(metadata.get("normalized_question")),
        "title": _field_text(metadata.get("title")),
        "section_path": _field_text(metadata.get("section_path")),
        "text": _field_text(text),
    }
    query_terms = _terms(query)
    query_base_terms = _base_terms(query)
    matched_terms: List[str] = []
    matched_fields: List[str] = []
    synonym_matches: List[Dict[str, str]] = []
    score = 0.0
    for term in query_terms:
        if _is_number_term(term):
            continue
        for field_name, field_value in fields.items():
            if not _contains(field_value, term):
                continue
            if _norm(term) not in matched_terms and len(matched_terms) < _MAX_TERMS:
                matched_terms.append(_norm(term))
            if field_name not in matched_fields and len(matched_fields) < _MAX_FIELDS:
                matched_fields.append(field_name)
            score += _field_match_weight(term, field_name)
            break

    for query_term in query_base_terms:
        for synonym in _SYNONYMS.get(_norm(query_term), []):
            for field_name in ("question_text", "normalized_question", "answer_text", "text"):
                if not _contains(fields[field_name], synonym):
                    continue
                evidence = {
                    "query_term": _norm(query_term),
                    "matched_synonym": _norm(synonym),
                    "field": field_name,
                }
                if evidence not in synonym_matches:
                    synonym_matches.append(evidence)
                if _norm(query_term) not in matched_terms and len(matched_terms) < _MAX_TERMS:
                    matched_terms.append(_norm(query_term))
                if _norm(synonym) not in matched_terms and len(matched_terms) < _MAX_TERMS:
                    matched_terms.append(_norm(synonym))
                if field_name not in matched_fields and len(matched_fields) < _MAX_FIELDS:
                    matched_fields.append(field_name)
                score += _field_match_weight(synonym, field_name) * 0.7
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

    query_neg = _has_negation(query)
    candidate_neg = _has_negation(fields["question_text"] + " " + fields["answer_text"])
    negation_conflict = query_neg != candidate_neg and (query_neg or candidate_neg)
    if negation_conflict:
        score = max(0.0, score - 0.04)

    return {
        "keyword_score": round(min(score, 1.0), 4),
        "matched_terms": matched_terms,
        "matched_fields": matched_fields,
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
