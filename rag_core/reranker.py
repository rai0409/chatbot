from __future__ import annotations

import re
from dataclasses import dataclass
from typing import List, Sequence

import config
from rag_core.ja_text import extract_salient_terms_ja, normalize_japanese_text
from rag_core.retrieval import RetrievedChunk


_ALNUM_TERM_PATTERN = re.compile(r"[a-z0-9][a-z0-9._:/-]{1,}")
_CODE_TRAILING_SEPARATORS = "._:/-"


@dataclass(frozen=True)
class _QuerySignals:
    quoted_code_terms: List[str]
    quoted_text_terms: List[str]
    id_terms: List[str]
    alnum_terms: List[str]
    katakana_terms: List[str]
    kanji_terms: List[str]
    short_lookup_terms: List[str]


def _normalize(text: str) -> str:
    return normalize_japanese_text(text or "").lower()


def _unique_preserve(tokens: Sequence[str]) -> List[str]:
    seen = set()
    out = []
    for tok in tokens:
        if not tok:
            continue
        if tok in seen:
            continue
        seen.add(tok)
        out.append(tok)
    return out


def _extract_quoted_terms(text: str) -> List[str]:
    norm = normalize_japanese_text(text or "")
    terms = []
    terms += re.findall(r"「([^」]+)」", norm)
    terms += re.findall(r'"([^"]+)"', norm)
    terms += re.findall(r"'([^']+)'", norm)
    return _unique_preserve([_normalize(t) for t in terms if _normalize(t)])


def _is_code_like_term(term: str) -> bool:
    if not _ALNUM_TERM_PATTERN.fullmatch(term or ""):
        return False
    return bool(re.search(r"\d", term))


def _extract_code_like_tokens(text: str) -> set[str]:
    tokens: set[str] = set()
    for raw in _ALNUM_TERM_PATTERN.findall(text):
        token = raw.rstrip(_CODE_TRAILING_SEPARATORS)
        if _ALNUM_TERM_PATTERN.fullmatch(token):
            tokens.add(token)
    return tokens


def _extract_short_lookup_terms(question: str) -> List[str]:
    norm = normalize_japanese_text(question or "").strip()
    if not norm:
        return []
    has_lookup_marker = any(
        marker in norm for marker in ("とは", "って何", "の意味", "の定義", "の仕様", "の確認方法")
    )
    if not has_lookup_marker and re.search(r"\s", norm):
        return []
    core = re.sub(r"[?？。!！]", "", norm)
    core = core.strip().strip("「」\"'")
    core = re.sub(r"\s+", "", core)
    for suffix in ("とは何か", "とは", "って何", "の意味", "の定義", "の仕様", "の確認方法"):
        if core.endswith(suffix):
            core = core[: -len(suffix)]
            break
    core = _normalize(core.strip("「」\"'"))
    if not (2 <= len(core) <= 12):
        return []
    if not (re.search(r"\d", core) or re.search(r"[一-龥々〆〤ァ-ヴー]{2,}", core)):
        return []
    return [core]


def _build_query_signals(question: str) -> _QuerySignals:
    quoted_terms = _extract_quoted_terms(question)
    quoted_code_terms = [t for t in quoted_terms if _is_code_like_term(t)]
    quoted_text_terms = [t for t in quoted_terms if not _is_code_like_term(t)]
    salient = _unique_preserve([_normalize(t) for t in extract_salient_terms_ja(question)])
    alnum_terms = [t for t in salient if _ALNUM_TERM_PATTERN.fullmatch(t)]
    id_terms = [t for t in alnum_terms if re.search(r"\d", t)]
    katakana_terms = [
        t
        for t in salient
        if len(t) >= 3 and re.fullmatch(r"[ァ-ヴー]{2,}(?:[一-龥々〆〤]{1,})?", t)
    ]
    kanji_terms = [t for t in salient if len(t) >= 3 and re.fullmatch(r"[一-龥々〆〤]{2,}", t)]
    return _QuerySignals(
        quoted_code_terms=_unique_preserve(quoted_code_terms),
        quoted_text_terms=_unique_preserve(quoted_text_terms),
        id_terms=_unique_preserve(id_terms),
        alnum_terms=_unique_preserve(alnum_terms),
        katakana_terms=_unique_preserve(katakana_terms),
        kanji_terms=_unique_preserve(kanji_terms),
        short_lookup_terms=_extract_short_lookup_terms(question),
    )


def _normalize_meta_text(value) -> str:
    if isinstance(value, list):
        return _normalize(" ".join(str(v) for v in value if str(v).strip()))
    if value is None:
        return ""
    return _normalize(str(value))


def _meta_alias_blob(meta: dict) -> str:
    aliases = meta.get("aliases")
    if aliases is None:
        aliases = meta.get("alias")
    if isinstance(aliases, list):
        values = [str(v).strip() for v in aliases if str(v).strip()]
        return _normalize(" ".join(values))
    return _normalize(str(aliases or ""))


def _doc_type_value(meta: dict) -> str:
    return _normalize(str(meta.get("doc_type") or ""))


def _is_faq_like(doc_type: str) -> bool:
    return doc_type in {"faq", "glossary", "faq_glossary"}


def _is_procedure_like(doc_type: str) -> bool:
    return doc_type in {"procedure", "howto", "procedure_howto", "manual"}


def rerank_chunks(
    question: str,
    chunks: Sequence[RetrievedChunk],
    intent: str | None = None,
) -> List[RetrievedChunk]:
    items = list(chunks)
    if len(items) <= 1:
        return items

    q = _build_query_signals(question)
    lexical_query_terms = _unique_preserve(q.alnum_terms + q.katakana_terms + q.kanji_terms)
    if not (q.quoted_code_terms or q.quoted_text_terms or lexical_query_terms or q.short_lookup_terms):
        return items

    scored = []
    for idx, ch in enumerate(items):
        meta = dict(ch.metadata or {})
        norm_text = _normalize(ch.text)
        code_tokens = _extract_code_like_tokens(norm_text)
        title_norm = _normalize_meta_text(meta.get("title"))
        section_norm = _normalize_meta_text(meta.get("section_path"))
        question_norm = _normalize_meta_text(meta.get("faq_question") or meta.get("question"))
        searchable_norm = _normalize_meta_text(meta.get("searchable_text"))
        alias_norm = _meta_alias_blob(meta)
        all_text_norm = " ".join(
            x for x in [norm_text, searchable_norm, title_norm, section_norm, question_norm, alias_norm] if x
        )
        all_code_tokens = _extract_code_like_tokens(all_text_norm)
        doc_type = _doc_type_value(meta)
        metadata_terms = _unique_preserve(
            q.short_lookup_terms + q.quoted_text_terms + q.katakana_terms + q.kanji_terms + q.alnum_terms
        )

        quoted_code_hits = sum(1 for t in q.quoted_code_terms if t and t in code_tokens)
        quoted_text_hits = sum(1 for t in q.quoted_text_terms if t and t in norm_text)
        quoted_hits = quoted_code_hits + quoted_text_hits
        id_hits = sum(1 for t in q.id_terms if t and t in all_code_tokens)
        alnum_hits = sum(1 for t in q.alnum_terms if t and t in all_code_tokens)
        katakana_hits = sum(1 for t in q.katakana_terms if t and t in norm_text)
        kanji_hits = sum(1 for t in q.kanji_terms if t and t in norm_text)
        short_lookup_hits = sum(1 for t in q.short_lookup_terms if t and t in all_text_norm)
        title_hits = sum(1 for t in metadata_terms if t and t in title_norm)
        section_hits = sum(1 for t in metadata_terms if t and t in section_norm)
        question_hits = sum(
            1 for t in metadata_terms if t and t in question_norm
        )
        alias_hits = sum(1 for t in q.alnum_terms + q.short_lookup_terms if t and t in alias_norm)
        exact_code_hits = sum(
            1 for t in q.id_terms + q.quoted_code_terms if t and t in all_code_tokens
        )

        has_strong = quoted_hits > 0 or id_hits > 0
        lexical_hits = alnum_hits + katakana_hits + kanji_hits

        boost = 0
        if quoted_hits:
            boost += 2 + min(1, quoted_hits - 1)
        if id_hits:
            boost += 1
        elif alnum_hits:
            boost += 2
        if katakana_hits:
            boost += 2
        if kanji_hits:
            boost += 2
        if short_lookup_hits:
            boost += 1
        if title_hits:
            boost += min(2, title_hits)
        if section_hits:
            boost += 1
        if question_hits:
            boost += 2
        if alias_hits:
            boost += 1
        if exact_code_hits:
            boost += 1

        if short_lookup_hits and _is_faq_like(doc_type):
            boost += int(getattr(config, "RERANK_BOOST_FAQ_SHORT_LOOKUP", 2))
        if intent == "procedure" and _is_procedure_like(doc_type):
            boost += int(getattr(config, "RERANK_BOOST_PROCEDURE_DOC_TYPE", 1))

        # For broad/ambiguous questions, avoid aggressive moves from a single weak match.
        if not has_strong and len(lexical_query_terms) > 1 and lexical_hits < 2 and title_hits == 0 and question_hits == 0:
            boost = 0

        if intent == "other" and not has_strong:
            boost = min(boost, int(getattr(config, "RERANK_MAX_LIFT_OTHER_WEAK", 2)))

        max_lift = int(getattr(config, "RERANK_MAX_LIFT_STRONG", 3)) if has_strong else int(
            getattr(config, "RERANK_MAX_LIFT_WEAK", 2)
        )
        lift = min(boost, max_lift)
        target_rank = max(0, idx - lift)

        scored.append(
            (
                target_rank,
                0 if has_strong else 1,
                -(quoted_hits + id_hits + exact_code_hits),
                -short_lookup_hits,
                -(title_hits + section_hits + question_hits + alias_hits),
                -lexical_hits,
                idx,
                ch,
            )
        )

    scored.sort()
    return [it[-1] for it in scored]
