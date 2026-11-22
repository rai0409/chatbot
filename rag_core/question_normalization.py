from __future__ import annotations

import re
import unicodedata


_PUNCT_TRANSLATION = str.maketrans(
    {
        "\u3000": " ",
        "？": "?",
        "！": "!",
        "“": '"',
        "”": '"',
        "„": '"',
        "‟": '"',
        "’": "'",
        "‘": "'",
        "‛": "'",
        "『": "「",
        "』": "」",
        "｢": "「",
        "｣": "」",
    }
)


_EXACT_KEY_DROP_RE = re.compile(r"[\"'“”‘’「」『』（）()\[\]【】、。，．,.・:：;；!?！？‐-‒–—―ー－\-\s]")


def exact_match_key(text: str) -> str:
    """Strong key for the approved-QA exact index: NFKC, lower case, and all
    quotes, brackets, punctuation, dashes, long vowel marks and spaces removed.
    Used by the exact index builder and lookup (one definition)."""
    value = unicodedata.normalize("NFKC", str(text or "")).replace("\u3000", " ").lower()
    # Retain separators that carry numeric or Japanese word meaning.
    out = []
    for i, char in enumerate(value):
        left = value[i - 1] if i else ""
        right = value[i + 1] if i + 1 < len(value) else ""
        numeric = bool(left and right and left.isdecimal() and right.isdecimal())
        if numeric and char in ".-‐‑‒–—―−":
            out.append("." if char == "." else "-")
        elif char == "ー" and right and "ァ" <= right <= "ヺ":
            out.append(char)
        elif not _EXACT_KEY_DROP_RE.fullmatch(char):
            out.append(char)
    return "".join(out)


def normalize_question_for_exact_match(question: str) -> str:
    """Normalize only stable surface-form differences for exact approved-QA lookup."""
    text = unicodedata.normalize("NFKC", question or "")
    text = text.translate(_PUNCT_TRANSLATION)
    text = re.sub(r"\s+", " ", text).strip()
    text = re.sub(r"\s+([?!.。、「」])", r"\1", text)
    text = re.sub(r"([「])\s+", r"\1", text)
    text = re.sub(r"\s+([」])", r"\1", text)
    return text
