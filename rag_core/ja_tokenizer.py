"""Japanese tokenization for the keyword (BM25) index.

Sudachi tokens (normalized form, content parts of speech only) plus
identifier tokens and character bigrams of katakana/kanji runs, so inflected
forms match their lemma (辞めたら -> 辞める) while unknown words and compounds
still match partially. Only the lexical index and lexical queries use these
tokens; dense embeddings never see them.
"""

from __future__ import annotations

import json
import logging
import re
import threading
from threading import Lock
from typing import Any, Dict, List

import config
from rag_core.ja_text import normalize_for_index

logger = logging.getLogger(__name__)

TOKENIZER_VERSION = "sudachi-bigram-v1"

# Function words and symbols carry no retrieval signal once Sudachi has split
# the text; content words (名詞, 動詞, 形容詞, 形状詞, 副詞, ...) are kept.
_EXCLUDED_POS = frozenset({"助詞", "助動詞", "補助記号", "記号", "空白", "感動詞", "接続詞"})
# sudachi.rs rejects inputs above a byte limit; 4,000 characters stays well
# below it even when every character is 3 bytes in UTF-8.
_MAX_SUDACHI_CHARS = 4000
_CODE_RE = re.compile(r"[a-z0-9][a-z0-9._:/-]{1,}")
_CODE_TRAILING_SEPARATORS = "._:/-"
_CODE_SEPARATOR_RE = re.compile(r"[._:/-]")
_BIGRAM_RUN_RE = re.compile(r"[ァ-ヴー一-龥々〆〤]{2,}")
_SINGLE_HIRAGANA_RE = re.compile(r"[ぁ-ん]")
_NO_WORD_CHAR_RE = re.compile(r"[^0-9a-zぁ-んァ-ヴー一-龥々〆〤]+")
_SENTENCE_BREAK_RE = re.compile(r"(?<=[。.!?\n])")

_VALID_MODES = ("auto", "sudachi", "heuristic")

_STATE_LOCK = Lock()
_STATE: Dict[str, Any] = {"key": None, "tokenizer": None, "owner": None, "error": None, "warned": False, "generation": 0}
# A SudachiPy tokenizer must not be used by two threads at once ("Already
# borrowed" under concurrent requests): every thread gets its own tokenizer,
# all created from one loaded dictionary.
_LOCAL = threading.local()
_DICTIONARIES: Dict[str, Any] = {}


def reset_cache() -> None:
    with _STATE_LOCK:
        _STATE.update({"key": None, "tokenizer": None, "owner": None, "error": None, "warned": False,
                       "generation": _STATE["generation"] + 1})


def _configured_mode() -> str:
    mode = str(getattr(config, "KEYWORD_TOKENIZER", "auto") or "auto").strip().lower()
    if mode not in _VALID_MODES:
        raise ValueError(
            f"Unknown KEYWORD_TOKENIZER '{mode}' (supported: {', '.join(_VALID_MODES)})"
        )
    return mode


def _create_sudachi_tokenizer(dict_name: str, split_mode: str):
    from sudachipy import Dictionary, SplitMode

    mode = getattr(SplitMode, split_mode.strip().upper(), None)
    if mode is None:
        raise ValueError(f"Unknown SUDACHI_SPLIT_MODE '{split_mode}' (supported: A, B, C)")
    dictionary = _DICTIONARIES.get(dict_name)
    if dictionary is None:
        dictionary = _DICTIONARIES[dict_name] = Dictionary(dict=dict_name)
    return dictionary.create(mode=mode)


def _get_sudachi():
    key = (
        str(getattr(config, "SUDACHI_DICT", "core") or "core"),
        str(getattr(config, "SUDACHI_SPLIT_MODE", "A") or "A"),
    )
    thread = threading.get_ident()
    with _STATE_LOCK:
        if _STATE["key"] != key:
            _STATE.update({"key": key, "tokenizer": None, "owner": None, "error": None,
                           "generation": _STATE["generation"] + 1})
            try:
                _STATE["tokenizer"] = _create_sudachi_tokenizer(*key)
                _STATE["owner"] = thread
            except Exception as exc:
                # Remember the failure so a missing dependency is probed once
                # per setting, not once per tokenized chunk.
                _STATE["error"] = exc
        if _STATE["tokenizer"] is None:
            raise RuntimeError("Sudachi tokenizer is unavailable") from _STATE["error"]
        generation = _STATE["generation"]
        if getattr(_LOCAL, "generation", None) != generation:
            _LOCAL.tokenizer = _STATE["tokenizer"] if _STATE["owner"] == thread else _create_sudachi_tokenizer(*key)
            _LOCAL.generation = generation
        return _LOCAL.tokenizer


def _warn_fallback_once(exc: Exception) -> None:
    with _STATE_LOCK:
        if _STATE["warned"]:
            return
        _STATE["warned"] = True
    cause = exc.__cause__ or exc
    logger.warning(
        "keyword_tokenizer_fallback %s",
        json.dumps(
            {"configured": "auto", "active": "heuristic", "error_type": type(cause).__name__},
            ensure_ascii=False,
        ),
    )


def active_tokenizer_name() -> str:
    """Return "sudachi" or "heuristic" for the configured KEYWORD_TOKENIZER."""
    mode = _configured_mode()
    if mode == "heuristic":
        return "heuristic"
    try:
        _get_sudachi()
    except RuntimeError as exc:
        if mode == "sudachi":
            raise RuntimeError(
                "KEYWORD_TOKENIZER=sudachi but SudachiPy/SudachiDict could not be loaded. "
                "Install requirements-ja.txt or set KEYWORD_TOKENIZER=heuristic."
            ) from exc
        _warn_fallback_once(exc)
        return "heuristic"
    return "sudachi"


def code_tokens(normalized: str) -> List[str]:
    """Identifier tokens plus separator-free variants: zz-10 -> zz-10, zz10."""
    out: List[str] = []
    for raw in _CODE_RE.findall(normalized):
        token = raw.rstrip(_CODE_TRAILING_SEPARATORS)
        if len(token) < 2:
            continue
        out.append(token)
        compact = _CODE_SEPARATOR_RE.sub("", token)
        if compact != token and len(compact) >= 2:
            out.append(compact)
    return out


def bigram_tokens(normalized: str) -> List[str]:
    """Character bigrams of katakana/kanji runs, namespaced away from lemmas."""
    out: List[str] = []
    for run in _BIGRAM_RUN_RE.findall(normalized):
        for i in range(len(run) - 1):
            out.append("2g:" + run[i : i + 2])
    return out


def _split_for_sudachi(text: str) -> List[str]:
    if len(text) <= _MAX_SUDACHI_CHARS:
        return [text]
    pieces: List[str] = []
    current = ""
    for sentence in _SENTENCE_BREAK_RE.split(text):
        while len(sentence) > _MAX_SUDACHI_CHARS:
            if current:
                pieces.append(current)
                current = ""
            pieces.append(sentence[:_MAX_SUDACHI_CHARS])
            sentence = sentence[_MAX_SUDACHI_CHARS:]
        if current and len(current) + len(sentence) > _MAX_SUDACHI_CHARS:
            pieces.append(current)
            current = ""
        current += sentence
    if current:
        pieces.append(current)
    return pieces


def morpheme_tokens(normalized: str) -> List[str]:
    tokenizer = _get_sudachi()
    out: List[str] = []
    for piece in _split_for_sudachi(normalized):
        for morpheme in tokenizer.tokenize(piece):
            pos = morpheme.part_of_speech()
            if pos and pos[0] in _EXCLUDED_POS:
                continue
            form = str(morpheme.normalized_form() or morpheme.surface()).strip().lower()
            if not form or _SINGLE_HIRAGANA_RE.fullmatch(form) or _NO_WORD_CHAR_RE.fullmatch(form):
                continue
            out.append(form)
    return out


def sudachi_tokens(text: str) -> List[str]:
    """Index/query tokens: Sudachi lemmas + identifiers + optional bigrams.

    Term frequency is preserved (duplicates kept) so BM25 can use it; callers
    de-duplicate query tokens.
    """
    normalized = normalize_for_index(text)
    if not normalized:
        return []
    tokens = morpheme_tokens(normalized)
    tokens.extend(code_tokens(normalized))
    if bool(getattr(config, "KEYWORD_NGRAM_ENABLED", True)):
        tokens.extend(bigram_tokens(normalized))
    return tokens
