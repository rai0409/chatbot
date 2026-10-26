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
    return _EXACT_KEY_DROP_RE.sub("", value)


def normalize_question_for_exact_match(question: str) -> str:
    """Normalize only stable surface-form differences for exact approved-QA lookup."""
    text = unicodedata.normalize("NFKC", question or "")
    text = text.translate(_PUNCT_TRANSLATION)
    text = re.sub(r"\s+", " ", text).strip()
    text = re.sub(r"\s+([?!.。、「」])", r"\1", text)
    text = re.sub(r"([「])\s+", r"\1", text)
    text = re.sub(r"\s+([」])", r"\1", text)
    return text
