from __future__ import annotations

import re
import unicodedata
from typing import List


_PUNCT_TRANSLATION = str.maketrans(
    {
        "“": '"',
        "”": '"',
        "„": '"',
        "‟": '"',
        "’": "'",
        "‘": "'",
        "‛": "'",
        "‐": "-",
        "‑": "-",
        "‒": "-",
        "–": "-",
        "—": "-",
        "―": "-",
        "−": "-",
    }
)


def normalize_japanese_text(text: str) -> str:
    out = unicodedata.normalize("NFKC", text or "")
    out = out.replace("\u3000", " ")
    out = out.translate(_PUNCT_TRANSLATION)
    out = re.sub(r"\s+", " ", out).strip()
    return out


def _mask_spans(text: str, spans: List[tuple[int, int]]) -> str:
    if not spans:
        return text
    chars = list(text)
    for start, end in spans:
        for i in range(start, min(end, len(chars))):
            chars[i] = " "
    return "".join(chars)


def extract_salient_terms_ja(text: str) -> List[str]:
    norm = normalize_japanese_text(text)
    terms: List[str] = []
    masked = norm

    quoted_spans: List[tuple[int, int]] = []
    for m in re.finditer(r"「([^」]+)」|\"([^\"]+)\"|'([^']+)'", norm):
        span = m.group(1) or m.group(2) or m.group(3) or ""
        t = normalize_japanese_text(span)
        if t:
            terms.append(t)
        quoted_spans.append(m.span())
    masked = _mask_spans(masked, quoted_spans)

    def _extract_and_mask(
        pattern: str,
        *,
        left_block: str | None = None,
        right_block: str | None = None,
    ) -> None:
        nonlocal masked
        spans: List[tuple[int, int]] = []
        for m in re.finditer(pattern, masked):
            start, end = m.span()
            left = masked[start - 1] if start > 0 else ""
            right = masked[end] if end < len(masked) else ""
            if left_block and left and re.fullmatch(left_block, left):
                continue
            if right_block and right and re.fullmatch(right_block, right):
                continue
            t = normalize_japanese_text(m.group(0))
            if t:
                terms.append(t)
            spans.append((start, end))
        masked = _mask_spans(masked, spans)

    jp_word = r"[A-Za-z0-9ぁ-んァ-ヴー一-龥々〆〤]"
    katakana_or_kanji = r"[ァ-ヴー一-龥々〆〤]"
    non_katakana_boundary = r"[A-Za-z0-9ぁ-ん一-龥々〆〤]"
    non_kanji_boundary = r"[A-Za-z0-9ぁ-んァ-ヴー]"

    _extract_and_mask(
        r"[A-Za-z0-9][A-Za-z0-9._:/-]{1,}",
        left_block=jp_word,
        right_block=jp_word,
    )
    _extract_and_mask(
        r"[ァ-ヴー]{2,}[一-龥々〆〤]{1,}",
        left_block=katakana_or_kanji,
        right_block=katakana_or_kanji,
    )
    _extract_and_mask(
        r"[ァ-ヴー]{2,}",
        left_block=non_katakana_boundary,
        right_block=non_katakana_boundary,
    )
    _extract_and_mask(
        r"[一-龥々〆〤]{2,}",
        left_block=non_kanji_boundary,
        right_block=non_kanji_boundary,
    )

    out: List[str] = []
    seen = set()
    for term in terms:
        t = normalize_japanese_text(term)
        if not t:
            continue
        if re.fullmatch(r"[ぁ-ん]{1,3}", t):
            continue
        if t in seen:
            continue
        seen.add(t)
        out.append(t)
    return out
