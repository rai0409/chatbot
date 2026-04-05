from __future__ import annotations

import re
from dataclasses import dataclass
from typing import List, Sequence

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
    )


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
    if not (q.quoted_code_terms or q.quoted_text_terms or lexical_query_terms):
        return items

    scored = []
    for idx, ch in enumerate(items):
        norm_text = _normalize(ch.text)
        code_tokens = _extract_code_like_tokens(norm_text)
        quoted_code_hits = sum(1 for t in q.quoted_code_terms if t and t in code_tokens)
        quoted_text_hits = sum(1 for t in q.quoted_text_terms if t and t in norm_text)
        quoted_hits = quoted_code_hits + quoted_text_hits
        id_hits = sum(1 for t in q.id_terms if t and t in code_tokens)
        alnum_hits = sum(1 for t in q.alnum_terms if t and t in code_tokens)
        katakana_hits = sum(1 for t in q.katakana_terms if t and t in norm_text)
        kanji_hits = sum(1 for t in q.kanji_terms if t and t in norm_text)

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

        # For broad/ambiguous questions, avoid aggressive moves from a single weak match.
        if not has_strong and len(lexical_query_terms) > 1 and lexical_hits < 2:
            boost = 0

        if intent == "other" and not has_strong:
            boost = min(boost, 2)

        max_lift = 3 if has_strong else 2
        lift = min(boost, max_lift)
        target_rank = max(0, idx - lift)

        scored.append(
            (
                target_rank,
                0 if has_strong else 1,
                -(quoted_hits + id_hits),
                -lexical_hits,
                idx,
                ch,
            )
        )

    scored.sort()
    return [it[-1] for it in scored]
