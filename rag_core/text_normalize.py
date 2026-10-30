"""Versioned, rule-by-rule text normalization profiles.

Every rule is a named pure function so a change can be traced and tested:
normalize(text, profile, trace=True) also returns the rules that changed the
text. Profiles:
- display: invisible-character and whitespace cleanup only (keeps width/case)
- dense:   NFKC + cleanup (embedding models are trained on raw text)
- index:   dense + dash/quote/wave-dash folding, katakana long-vowel hyphens,
           thousands separators, kanji numerals in numeric context, lowercase
- match:   index + trailing katakana long vowel removal (サーバー -> サーバ)
All profiles are idempotent (normalize(normalize(x)) == normalize(x)).
"""

from __future__ import annotations

import re
import unicodedata
from typing import Callable, Dict, List, Tuple, Union

from rag_core.quantities import kanji_numeral_spans

PROFILE_VERSION = "text-normalize-v1"

_INVISIBLE_RE = re.compile(r"[​‌‍⁠﻿\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_HSPACE_RE = re.compile(r"[ \t　 ]+")
_TRAILING_SPACE_RE = re.compile(r" +\n")
_MANY_NEWLINES_RE = re.compile(r"\n{3,}")
_DASH_QUOTE_TABLE = str.maketrans(
    {
        "“": '"', "”": '"', "„": '"', "‟": '"', "’": "'", "‘": "'", "‛": "'",
        "‐": "-", "‑": "-", "‒": "-", "–": "-", "—": "-", "―": "-", "−": "-",
        "〜": "~", "～": "~",
    }
)
# A hyphen written between katakana is a long-vowel mark (サ-バ -> サーバ).
_KATAKANA_HYPHEN_RE = re.compile(r"(?<=[ァ-ヴ])-(?=[ァ-ヴ])")
# Trailing long vowel of katakana words with 3+ preceding characters, like
# the kuromoji katakana stemmer (minimum length 4 including the mark).
_KATAKANA_FINAL_LONG_RE = re.compile(r"(?<=[ァ-ヴー]{3})ー(?![ァ-ヴー])")
_THOUSANDS_GROUP_RE = re.compile(r"(?<![\d,])\d{1,3}(?:,\d{3})+(?![\d,])")


def _nfkc(text: str) -> str:
    return unicodedata.normalize("NFKC", text)


def _strip_invisible(text: str) -> str:
    return _INVISIBLE_RE.sub("", text.replace("\r\n", "\n").replace("\r", "\n"))


def _spaces(text: str) -> str:
    text = _HSPACE_RE.sub(" ", text)
    text = _TRAILING_SPACE_RE.sub("\n", text)
    text = _MANY_NEWLINES_RE.sub("\n\n", text)
    return text.strip()


def _dash_quote(text: str) -> str:
    return text.translate(_DASH_QUOTE_TABLE)


def _katakana_hyphen(text: str) -> str:
    return _KATAKANA_HYPHEN_RE.sub("ー", text)


def _thousands(text: str) -> str:
    return _THOUSANDS_GROUP_RE.sub(lambda m: m.group(0).replace(",", ""), text)


def _kanji_numerals(text: str) -> str:
    spans = kanji_numeral_spans(text)
    if not spans:
        return text
    parts: List[str] = []
    cursor = 0
    for start, end, arabic in spans:
        parts.append(text[cursor:start])
        parts.append(arabic)
        cursor = end
    parts.append(text[cursor:])
    return "".join(parts)


def _lower(text: str) -> str:
    return text.lower()


def _katakana_final_long(text: str) -> str:
    return _KATAKANA_FINAL_LONG_RE.sub("", text)


Rule = Tuple[str, Callable[[str], str]]
_DISPLAY: Tuple[Rule, ...] = (("strip_invisible", _strip_invisible), ("spaces", _spaces))
_DENSE: Tuple[Rule, ...] = (("nfkc", _nfkc),) + _DISPLAY
_INDEX: Tuple[Rule, ...] = _DENSE + (
    ("dash_quote", _dash_quote),
    ("katakana_hyphen", _katakana_hyphen),
    ("thousands", _thousands),
    ("kanji_numerals", _kanji_numerals),
    ("lower", _lower),
)
PROFILES: Dict[str, Tuple[Rule, ...]] = {
    "display": _DISPLAY,
    "dense": _DENSE,
    "index": _INDEX,
    "match": _INDEX + (("katakana_final_long", _katakana_final_long),),
}


def normalize(text: str, profile: str = "index", trace: bool = False) -> Union[str, Tuple[str, List[str]]]:
    try:
        rules = PROFILES[profile]
    except KeyError:
        raise ValueError(f"Unknown normalization profile '{profile}' (supported: {', '.join(PROFILES)})") from None
    out = text or ""
    changed: List[str] = []
    for name, rule in rules:
        updated = rule(out)
        if updated != out:
            changed.append(name)
        out = updated
    return (out, changed) if trace else out
