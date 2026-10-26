from __future__ import annotations

import os
import time
from collections import OrderedDict
from threading import Lock
from typing import Any, Dict, Optional, Tuple

import config
from rag_core.question_normalization import normalize_question_for_exact_match


_LOCK = Lock()
# key -> (expires at, value). Stage 14: the local cache expires like Redis
# (ANSWER_CACHE_TTL_SECONDS), so a corpus changed outside the keyword file
# (e.g. a Qdrant-only update) stops being answered from the cache.
_CACHE: "OrderedDict[Tuple, Tuple[float, Dict[str, Any]]]" = OrderedDict()


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


def _pipeline_state() -> Tuple:
    # Everything that changes the answer for the same question and corpus:
    # generation mode and model, embedding and reranker models, keyword
    # tokenizer / normalization / engine. Any change starts a fresh key.
    from rag_core import embedding_provider
    from rag_core.ja_text import active_normalization_version

    try:
        generation_mode = config.resolve_chat_generation_mode()
    except Exception:  # noqa: BLE001
        generation_mode = "unknown"
    try:
        embed = embedding_provider.active_fingerprint()
        embed_state = tuple(sorted((str(k), str(v)) for k, v in dict(embed).items()))
    except Exception:  # noqa: BLE001
        embed_state = ("unknown",)
    return (
        generation_mode,
        str(getattr(config, "CHAT_MODEL", "") or ""),
        embed_state,
        bool(getattr(config, "CROSS_ENCODER_RERANK_ENABLED", False)),
        str(getattr(config, "CROSS_ENCODER_MODEL", "") or ""),
        str(getattr(config, "KEYWORD_TOKENIZER", "") or ""),
        str(getattr(config, "KEYWORD_INDEX_ENGINE", "") or ""),
        active_normalization_version(),
        os.getenv("QUERY_PREPROCESS_ENABLED", ""),
    )


def build_key(question: str, *, top_k: int, max_context_chars: int, tenant_id: str = "default") -> Tuple:
    return (
        str(tenant_id or "").strip() or "default",
        normalize_question_for_exact_match(question or ""),
        _corpus_state(),
        _pipeline_state(),
        int(top_k),
        int(max_context_chars),
    )


def _redis_backend() -> bool:
    return str(os.getenv("ANSWER_CACHE_BACKEND", "local")).strip().lower() == "redis"


def _redis_key(key: Tuple) -> str:
    import hashlib

    return "ac:" + hashlib.sha256(repr(key).encode("utf-8")).hexdigest()


def _ttl_seconds() -> int:
    try:
        return max(1, int(os.getenv("ANSWER_CACHE_TTL_SECONDS", "") or 3600))
    except ValueError:
        return 3600


def get(key: Tuple) -> Optional[Dict[str, Any]]:
    if not enabled():
        return None
    if _redis_backend():
        import json

        from webapi import state_backend

        try:
            raw = state_backend.redis_client().get(_redis_key(key))
        except state_backend.redis_errors() as exc:
            state_backend.warn_degraded("redis_answer_cache", exc)
            return None  # Redis down: answer without the cache
        return json.loads(raw) if raw else None
    with _LOCK:
        entry = _CACHE.get(key)
        if entry is None:
            return None
        expires_at, value = entry
        if time.time() >= expires_at:
            _CACHE.pop(key, None)
            return None
        _CACHE.move_to_end(key)
        return value


def put(key: Tuple, value: Dict[str, Any]) -> None:
    if not enabled():
        return
    if _redis_backend():
        import json

        from webapi import state_backend

        try:
            state_backend.redis_client().set(
                _redis_key(key), json.dumps(value, ensure_ascii=False, default=str), ex=_ttl_seconds()
            )
        except state_backend.redis_errors() as exc:
            state_backend.warn_degraded("redis_answer_cache", exc)
        return
    with _LOCK:
        _CACHE[key] = (time.time() + _ttl_seconds(), value)
        _CACHE.move_to_end(key)
        while len(_CACHE) > _max_entries():
            _CACHE.popitem(last=False)


def size() -> int:
    if _redis_backend():
        from webapi import state_backend

        return sum(1 for _ in state_backend.redis_client().scan_iter(match="ac:*", count=500))
    with _LOCK:
        return len(_CACHE)


def clear() -> None:
    # Redis: removes every answer-cache key (all nodes share them).
    if _redis_backend():
        from webapi import state_backend

        client = state_backend.redis_client()
        batch = []
        for key in client.scan_iter(match="ac:*", count=500):
            batch.append(key)
            if len(batch) >= 500:
                client.delete(*batch)
                batch = []
        if batch:
            client.delete(*batch)
    with _LOCK:
        _CACHE.clear()
