"""Content-word extraction shared by answer selection, guards and query preprocessing (N6).

With Sudachi available, terms are morpheme lemmas (normalized form) without
particles/auxiliaries, so inflected forms meet: 辞めたら / 辞めた -> 辞める.
Without Sudachi the historical regex is used (alnum runs, katakana runs,
kanji runs of 2+ characters). Both paths apply the index normalization first.
"""

from __future__ import annotations

import re
from typing import Iterable, List

from rag_core import ja_tokenizer
from rag_core.ja_text import normalize_for_index

_REGEX_TERM_RE = re.compile(r"[a-z0-9][a-z0-9._:/-]{1,}|[ァ-ヴー]{2,}|[一-龥々〆〤]{2,}")
_STOP_TERMS = frozenset(
    {
        "です", "ます", "ください", "下さい", "何で", "何です", "原因", "概要", "説明",
        "ある", "いる", "する", "なる", "できる", "思う", "言う", "こと", "もの", "とき",
        "ため", "よう", "これ", "それ", "あれ", "どれ", "ここ", "そこ", "どこ",
    }
)
_KANJI_OR_KATAKANA_RE = re.compile(r"[ァ-ヴー一-龥々〆〤]")


def _unique(values: Iterable[str]) -> List[str]:
    return list(dict.fromkeys(v for v in values if v))


def regex_terms(text: str) -> List[str]:
    norm = normalize_for_index(text or "")
    return _unique(t for t in _REGEX_TERM_RE.findall(norm) if len(t) >= 2 and t not in _STOP_TERMS)


def morph_available() -> bool:
    try:
        return ja_tokenizer.active_tokenizer_name() == "sudachi"
    except RuntimeError:
        return False


def morph_terms(text: str) -> List[str]:
    """Lemmas of content words; one-character lemmas are kept only for kanji."""
    norm = normalize_for_index(text or "")
    if not norm:
        return []
    terms: List[str] = []
    for form in ja_tokenizer.morpheme_tokens(norm):
        if form in _STOP_TERMS:
            continue
        if len(form) == 1 and not _KANJI_OR_KATAKANA_RE.fullmatch(form):
            continue
        terms.append(form)
    terms.extend(ja_tokenizer.code_tokens(norm))
    return _unique(terms)


def reading_tokens(text: str) -> List[str]:
    """Katakana readings of content words, namespaced "r:" (N5 reading lane).

    Homophones written differently (保険 / 保健, 制度 / 精度) share a reading,
    so a reading index finds documents the surface tokens miss.
    """
    if not morph_available():
        return []
    norm = normalize_for_index(text or "")
    tokenizer = ja_tokenizer._get_sudachi()
    out: List[str] = []
    for piece in ja_tokenizer._split_for_sudachi(norm):
        for morpheme in tokenizer.tokenize(piece):
            pos = morpheme.part_of_speech()
            if pos and pos[0] in ja_tokenizer._EXCLUDED_POS:
                continue
            reading = str(morpheme.reading_form() or "")
            if len(reading) >= 2 and reading != "キゴウ":
                out.append("r:" + reading)
    return out


_PROCEDURE_NOUNS = {"方法", "仕方", "手順", "やり方", "手続き"}


def has_procedure_form(text: str) -> bool:
    """Verb followed by たい / には / a procedure noun (N7). False without Sudachi."""
    if not morph_available():
        return False
    morphemes = list(ja_tokenizer._get_sudachi().tokenize(normalize_for_index(text or "")[:2000]))
    for i, morpheme in enumerate(morphemes[:-1]):
        pos = morpheme.part_of_speech()
        if not pos or pos[0] != "動詞":
            continue
        for following in morphemes[i + 1 : i + 3]:
            form = str(following.dictionary_form())
            fpos = following.part_of_speech()[0]
            if fpos == "助動詞" and form == "たい":
                return True
            if fpos == "助詞" and form in {"には", "に"} and following.surface() in {"には", "に"}:
                return True
            if fpos == "名詞" and form in _PROCEDURE_NOUNS:
                return True
    return False


def content_terms(text: str, mode: str = "auto") -> List[str]:
    """mode: "regex" (historical), "morph" (Sudachi, error if missing), "auto"."""
    if mode == "regex":
        return regex_terms(text)
    if mode == "morph" or (mode == "auto" and morph_available()):
        return morph_terms(text)
    return regex_terms(text)
