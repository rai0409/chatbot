from __future__ import annotations

import os
from collections import OrderedDict
from threading import Lock
from typing import Any, Dict, Optional, Tuple

import config
from rag_core.question_normalization import normalize_question_for_exact_match


_LOCK = Lock()
_CACHE: "OrderedDict[Tuple, Dict[str, Any]]" = OrderedDict()


def enabled() -> bool:
    return bool(getattr(config, "ANSWER_CACHE_ENABLED", False))


def _max_entries() -> int:
    try:
        value = int(getattr(config, "ANSWER_CACHE_MAX_ENTRIES", 256))
    except (TypeError, ValueError):
        value = 256
    return max(1, value)


def _file_state(path: str) -> Tuple:
    # Path identity plus mtime_ns/size; a missing file is an explicit state,
    # so missing -> present transitions also change the key.
    if not path:
        return ("no_path",)
    try:
        stat = os.stat(path)
    except OSError:
        return (str(path), "missing")
    return (str(path), int(stat.st_mtime_ns), int(stat.st_size))


def _corpus_state() -> Tuple:
    chunks_state = _file_state(str(getattr(config, "CHUNKS_JSONL_PATH", "") or ""))
    if getattr(config, "APPROVED_QA_ENABLED", False):
        approved_state = _file_state(str(getattr(config, "APPROVED_QA_PATH", "") or ""))
    else:
        approved_state = ("approved_qa_disabled",)
    return (chunks_state, approved_state)


def build_key(question: str, *, top_k: int, max_context_chars: int) -> Tuple:
    return (
        normalize_question_for_exact_match(question or ""),
        _corpus_state(),
        int(top_k),
        int(max_context_chars),
    )


def get(key: Tuple) -> Optional[Dict[str, Any]]:
    if not enabled():
        return None
    with _LOCK:
        value = _CACHE.get(key)
        if value is None:
            return None
        _CACHE.move_to_end(key)
        return value


def put(key: Tuple, value: Dict[str, Any]) -> None:
    if not enabled():
        return
    with _LOCK:
        _CACHE[key] = value
        _CACHE.move_to_end(key)
        while len(_CACHE) > _max_entries():
            _CACHE.popitem(last=False)


def size() -> int:
    with _LOCK:
        return len(_CACHE)


def clear() -> None:
    with _LOCK:
        _CACHE.clear()
