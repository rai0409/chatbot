from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Sequence

from rag_core.ja_text import extract_salient_terms_ja, normalize_japanese_text


_CODE_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/-]{1,}")
_CODE_TRAILING_SEPARATORS = "._:/-"
_MAX_MATCHED_TERMS = 20
_MAX_MATCHED_FIELDS = 12

_GENERIC_TERMS = {
    "これ",
    "それ",
    "あれ",
    "こと",
    "もの",
    "ため",
    "場合",
    "方法",
    "手順",
    "操作",
    "確認",
    "登録",
    "変更",
    "設定",
    "意味",
    "説明",
    "情報",
    "内容",
    "質問",
    "不明",
    "教えて",
    "ですか",
    "ますか",
}
_PROCEDURE_TERMS = {
    "手順",
    "方法",
    "やり方",
    "操作",
    "再設定",
    "変更",
    "登録",
    "確認",
    "申請",
    "更新",
}
_FAQ_TERMS = {"ですか", "ますか", "どうしたら", "何ですか", "なぜ", "いつ", "どこ"}


def _normalize(text: Any) -> str:
    return normalize_japanese_text(str(text or "")).lower()


def _compact(text: Any) -> str:
    return re.sub(r"\s+", "", _normalize(text))


def _unique_preserve(tokens: Sequence[str], *, limit: int | None = None) -> List[str]:
    seen = set()
    out: List[str] = []
    for raw in tokens:
        tok = _normalize(raw).strip()
        if not tok or tok in seen:
            continue
        seen.add(tok)
        out.append(tok)
        if limit is not None and len(out) >= limit:
            break
    return out


def _extract_quoted_terms(text: str) -> List[str]:
    norm = normalize_japanese_text(text or "")
    terms: List[str] = []
    terms += re.findall(r"「([^」]+)」", norm)
    terms += re.findall(r'"([^"]+)"', norm)
    terms += re.findall(r"'([^']+)'", norm)
    return _unique_preserve(terms)


def _extract_code_like_tokens(text: str) -> List[str]:
    tokens: List[str] = []
    for raw in _CODE_PATTERN.findall(_normalize(text)):
        token = raw.rstrip(_CODE_TRAILING_SEPARATORS)
        if _CODE_PATTERN.fullmatch(token):
            tokens.append(token)
    return _unique_preserve(tokens)


def _identifier_terms(text: str) -> List[str]:
    return [tok for tok in _extract_code_like_tokens(text) if re.search(r"\d", tok)]


def _field_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (list, tuple, set)):
        return _normalize(" ".join(str(v) for v in value if str(v).strip()))
    if isinstance(value, dict):
        return _normalize(" ".join(str(v) for v in value.values() if str(v).strip()))
    if isinstance(value, str):
        stripped = value.strip()
        if stripped and stripped[0] in "[{":
            try:
                parsed = json.loads(stripped)
            except Exception:
                parsed = None
            if parsed is not None:
                return _field_text(parsed)
        return _normalize(stripped)
    return _normalize(value)


def _short_lookup_core(query: str) -> str:
    core = re.sub(r"[?？。!！]", "", query or "")
    core = core.strip().strip("「」\"'")
    core = re.sub(r"\s+", "", core)
    for suffix in ("とは何か", "とは", "って何", "の意味", "の定義", "の仕様", "の確認方法"):
        if core.endswith(suffix):
            return _normalize(core[: -len(suffix)].strip("「」\"'"))
    return ""


def _salient_terms(query: str) -> List[str]:
    terms: List[str] = []
    terms.extend(_extract_quoted_terms(query))
    terms.extend(_identifier_terms(query))
    terms.extend(extract_salient_terms_ja(query))
    terms.extend(re.findall(r"[ァ-ヴー]{2,}", normalize_japanese_text(query or "")))
    terms.extend(re.findall(r"[一-龥々〆〤]{2,}", normalize_japanese_text(query or "")))
    core = _short_lookup_core(query)
    if core:
        terms.append(core)
    out = []
    for term in _unique_preserve(terms):
        compact = _compact(term)
        if not compact or compact in _GENERIC_TERMS:
            continue
        if re.fullmatch(r"[ぁ-ん]{1,3}", compact):
            continue
        out.append(term)
    return _unique_preserve(out, limit=_MAX_MATCHED_TERMS)


def classify_query_type(query: str, intent: str | None = None) -> str:
    q = normalize_japanese_text(query or "")
    q_compact = re.sub(r"\s+", "", q)
    if not q_compact:
        return "other"

    quoted_terms = _extract_quoted_terms(q)
    if quoted_terms:
        return "exact_lookup"

    id_terms = _identifier_terms(q)
    if id_terms:
        return "identifier"

    if _short_lookup_core(q):
        return "exact_lookup"

    if intent in {"reset", "change", "procedure"} or any(t in q for t in _PROCEDURE_TERMS):
        return "procedure"

    if q.endswith(("?", "？")) or any(t in q for t in _FAQ_TERMS):
        return "faq"

    salient = _salient_terms(q)
    if len(q_compact) <= 4 and len(salient) <= 1:
        return "ambiguous"
    if len(salient) <= 1 and len(q_compact) <= 8 and not any(t in q for t in _PROCEDURE_TERMS):
        return "ambiguous"
    if len(q_compact) >= 18 and not quoted_terms and not id_terms:
        return "broad"
    return "other"


def _score_add(
    *,
    amount: float,
    term: str,
    field: str,
    matched_terms: List[str],
    matched_fields: List[str],
) -> float:
    norm_term = _normalize(term)
    if norm_term and norm_term not in matched_terms and len(matched_terms) < _MAX_MATCHED_TERMS:
        matched_terms.append(norm_term)
    if field and field not in matched_fields and len(matched_fields) < _MAX_MATCHED_FIELDS:
        matched_fields.append(field)
    return amount


def _field_contains(field_value: str, term: str, *, strict_code: bool = False) -> bool:
    if not field_value or not term:
        return False
    norm_term = _normalize(term)
    if strict_code:
        return norm_term in set(_extract_code_like_tokens(field_value))
    return norm_term in field_value or _compact(norm_term) in _compact(field_value)


def score_keyword_match(
    query: str,
    text: str,
    metadata: dict | None = None,
    query_type: str | None = None,
) -> dict:
    meta = metadata or {}
    qtype = query_type or classify_query_type(query)
    quoted_terms = _extract_quoted_terms(query)
    identifier_terms = _identifier_terms(query)
    salient_terms = _salient_terms(query)
    katakana_terms = _unique_preserve(re.findall(r"[ァ-ヴー]{2,}", normalize_japanese_text(query or "")))
    kanji_terms = _unique_preserve(re.findall(r"[一-龥々〆〤]{2,}", normalize_japanese_text(query or "")))

    fields = {
        "text": _field_text(text),
        "title": _field_text(meta.get("title")),
        "section_path": _field_text(meta.get("section_path")),
        "doc_type": _field_text(meta.get("doc_type")),
        "chunk_role": _field_text(meta.get("chunk_role")),
        "parent_chunk_id": _field_text(meta.get("parent_chunk_id")),
        "source_doc": _field_text(meta.get("source_doc") or meta.get("doc")),
        "source_pages": _field_text(meta.get("source_pages") or meta.get("pages")),
    }
    text_blob = " ".join(v for v in fields.values() if v)

    matched_terms: List[str] = []
    matched_fields: List[str] = []
    signals: Dict[str, Any] = {
        "quoted_term_hit": False,
        "exact_phrase_hit": False,
        "identifier_hit": False,
        "katakana_hit": False,
        "kanji_compound_hit": False,
        "title_hit": False,
        "section_path_hit": False,
        "doc_type_boost": 0.0,
        "chunk_role_boost": 0.0,
    }

    score = 0.0
    for term in quoted_terms:
        for field_name in ("text", "title", "section_path"):
            if _field_contains(fields[field_name], term):
                score += _score_add(
                    amount=0.55 if field_name == "text" else 0.45,
                    term=term,
                    field=field_name,
                    matched_terms=matched_terms,
                    matched_fields=matched_fields,
                )
                signals["quoted_term_hit"] = True
                signals["exact_phrase_hit"] = True
                if field_name == "title":
                    signals["title_hit"] = True
                if field_name == "section_path":
                    signals["section_path_hit"] = True
                break

    for term in identifier_terms:
        strict_hit = any(_field_contains(fields[name], term, strict_code=True) for name in fields)
        if strict_hit:
            score += _score_add(
                amount=0.45,
                term=term,
                field="identifier",
                matched_terms=matched_terms,
                matched_fields=matched_fields,
            )
            signals["identifier_hit"] = True

    for term in salient_terms:
        if term in quoted_terms or term in identifier_terms:
            continue
        if _field_contains(fields["text"], term):
            score += _score_add(
                amount=0.14,
                term=term,
                field="text",
                matched_terms=matched_terms,
                matched_fields=matched_fields,
            )
            signals["exact_phrase_hit"] = True

    for term in katakana_terms:
        if _field_contains(fields["text"], term):
            score += _score_add(
                amount=0.12,
                term=term,
                field="text",
                matched_terms=matched_terms,
                matched_fields=matched_fields,
            )
            signals["katakana_hit"] = True

    for term in kanji_terms:
        if _field_contains(fields["text"], term):
            score += _score_add(
                amount=0.12,
                term=term,
                field="text",
                matched_terms=matched_terms,
                matched_fields=matched_fields,
            )
            signals["kanji_compound_hit"] = True

    metadata_terms = _unique_preserve(quoted_terms + identifier_terms + salient_terms)
    for term in metadata_terms:
        if _field_contains(fields["title"], term):
            score += _score_add(
                amount=0.28,
                term=term,
                field="title",
                matched_terms=matched_terms,
                matched_fields=matched_fields,
            )
            signals["title_hit"] = True
        if _field_contains(fields["section_path"], term):
            score += _score_add(
                amount=0.22,
                term=term,
                field="section_path",
                matched_terms=matched_terms,
                matched_fields=matched_fields,
            )
            signals["section_path_hit"] = True

    doc_type = fields["doc_type"]
    if qtype == "procedure" and doc_type in {"procedure", "howto", "procedure_howto", "manual"}:
        signals["doc_type_boost"] = 0.08
        score += signals["doc_type_boost"]
    elif qtype in {"faq", "exact_lookup"} and doc_type in {"faq", "glossary", "faq_glossary"}:
        signals["doc_type_boost"] = 0.08
        score += signals["doc_type_boost"]

    chunk_role = fields["chunk_role"]
    if chunk_role in {"child", "leaf"}:
        signals["chunk_role_boost"] = 0.03
        score += signals["chunk_role_boost"]
    elif chunk_role == "parent" and qtype in {"procedure", "broad"}:
        signals["chunk_role_boost"] = 0.02
        score += signals["chunk_role_boost"]

    # Catch exact full-query phrases after metadata boosts, without letting generic long queries dominate.
    full_query = re.sub(r"[?？。!！]", "", normalize_japanese_text(query or "")).strip()
    if 3 <= len(_compact(full_query)) <= 30 and _field_contains(text_blob, full_query):
        score += _score_add(
            amount=0.2,
            term=full_query,
            field="exact_phrase",
            matched_terms=matched_terms,
            matched_fields=matched_fields,
        )
        signals["exact_phrase_hit"] = True

    return {
        "keyword_score": round(min(score, 1.0), 4),
        "matched_terms": matched_terms[:_MAX_MATCHED_TERMS],
        "matched_fields": matched_fields[:_MAX_MATCHED_FIELDS],
        "signals": signals,
    }
