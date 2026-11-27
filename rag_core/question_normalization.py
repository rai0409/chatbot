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


def _is_katakana(char: str) -> bool:
    return bool(char) and "ァ" <= char <= "ヺ"


def _keep_long_vowel(value: str, index: int) -> bool:
    """One rule for the long vowel mark ー, shared by the exact key and the answer-cache key.

    Kept inside a katakana word, i.e. when a katakana letter follows (パスワード,
    ビール — so ビール and ビル stay distinct); dropped where the word ends
    (サーバー -> サーバ, ユーザーの -> ユーザの), which makes the optional
    loanword ending equal: サーバ / サーバー, ユーザ / ユーザー.
    Known limit: inside a compound written without a break (サーバーメンテ vs
    サーバメンテ) the mark is indistinguishable from a word-internal one
    (パスワード) and is kept; register such variants as approved aliases.
    """
    right = value[index + 1] if index + 1 < len(value) else ""
    return _is_katakana(right)


def fold_long_vowels(text: str) -> str:
    """Apply the long-vowel rule of exact_match_key to a text, keeping everything else.

    Used by the answer-cache question key so サーバ / サーバー share one key."""
    value = str(text or "")
    if "ー" not in value:
        return value
    return "".join(char for i, char in enumerate(value) if char != "ー" or _keep_long_vowel(value, i))


def exact_match_key(text: str) -> str:
    """Strong key for the approved-QA exact index: NFKC, lower case, and all
    quotes, brackets, punctuation, dashes, optional long vowel marks and spaces
    removed. Used by the exact index builder and lookup (one definition).

    The long vowel mark follows _keep_long_vowel, the same rule as the
    answer-cache question key (fold_long_vowels)."""
    value = unicodedata.normalize("NFKC", str(text or "")).replace("\u3000", " ").lower()
    # Retain separators that carry numeric or Japanese word meaning.
    out = []
    for i, char in enumerate(value):
        left = value[i - 1] if i else ""
        right = value[i + 1] if i + 1 < len(value) else ""
        numeric = bool(left and right and left.isdecimal() and right.isdecimal())
        if numeric and char in ".-‐‑‒–—―−":
            out.append("." if char == "." else "-")
        elif char == "ー":
            if _keep_long_vowel(value, i):
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
