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


def normalize_question_for_exact_match(question: str) -> str:
    """Normalize only stable surface-form differences for exact approved-QA lookup."""
    text = unicodedata.normalize("NFKC", question or "")
    text = text.translate(_PUNCT_TRANSLATION)
    text = re.sub(r"\s+", " ", text).strip()
    text = re.sub(r"\s+([?!.。、「」])", r"\1", text)
    text = re.sub(r"([「])\s+", r"\1", text)
    text = re.sub(r"\s+([」])", r"\1", text)
    return text
