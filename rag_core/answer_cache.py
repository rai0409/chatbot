from __future__ import annotations

import hashlib
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
    from rag_core import index_generation

    chunks_state = _file_state(index_generation.served_keyword_path())
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


# Settings that change the answer for the same question, corpus and models: retrieval fusion, boosts,
# guard distances, context limits, the LLM endpoint and output length. The environment-only switches
# that qa.py reads by name are listed too. A changed value starts a fresh key (only a digest is kept).
_SETTING_NAMES = (
    "ENABLE_HYBRID_RETRIEVAL", "VECTOR_TOP_K", "BM25_TOP_K", "HYBRID_RRF_K", "HYBRID_FUSION",
    "HYBRID_DENSE_WEIGHT", "HYBRID_KEYWORD_WEIGHT", "HYBRID_EXACT_WEIGHT",
    "CHILD_RETRIEVAL_OVERSAMPLE", "MAX_PARENT_EXPANDED_CHUNKS", "MAX_PARENT_CONTEXT_CHARS",
    "PROCEDURE_MAX_PER_PAGE", "CHANGE_RESET_MAX_PER_PAGE", "OTHER_QUERY_BOOST_TERMS",
    "KEYWORD_BOOST_ENABLED", "KEYWORD_BOOST_QUERY_TYPES", "KEYWORD_BOOST_MAX_DELTA",
    "PROFILE_BOOST_ENABLED", "PROFILE_BOOST_MAX_DELTA", "CROSS_ENCODER_RERANK_ENABLED",
    "MULTI_QUERY_ENABLED", "MULTI_QUERY_MAX_VARIANTS", "MULTI_QUERY_RRF_K", "MULTI_QUERY_VARIANT_PENALTY",
    "QUERY_ROUTING_ENABLED",
    "LLM_ENABLED", "LLM_TENANT_ALLOWLIST", "LLM_TENANT_DENYLIST", "LLM_FEATURES", "LLM_FEATURE_MAX_TOKENS",
    "GROUNDING_VERIFY_ENABLED", "GROUNDING_MIN_COVERAGE",
    "RAG_HARD_MAX_DIST", "RAG_HARD_MAX_DIST_PROCEDURE_DELTA", "RAG_SOFT_DIST_OTHER",
    "RAG_SOFT_DIST_RESET", "RAG_SOFT_DIST_CHANGE", "RAG_SOFT_DIST_PROCEDURE",
    "CROSS_ENCODER_TOP_N", "CROSS_ENCODER_MAX_LENGTH", "CROSS_ENCODER_SKIP_MARGIN",
    "OPENAI_BASE_URL", "CHAT_COMPLETION_MAX_TOKENS",
)
_SETTING_ENV_NAMES = (
    "INTENT_RULES", "COMPOSE_QUERY_MODE", "PREFER_SORT_MODE", "EXTRACTIVE_TERM_MODE",
    "EXTRACTIVE_EVIDENCE_RULE", "STAGED_RETRIEVAL_MARGIN", "QUERY_PREPROCESS_ENABLED",
)


def _settings_state() -> str:
    values = [(name, repr(getattr(config, name, None))) for name in _SETTING_NAMES]
    # a config attribute (set by an evaluation run) wins over the environment, as in qa._setting
    values += [(name, str(getattr(config, name)) if hasattr(config, name) else os.getenv(name, ""))
               for name in _SETTING_ENV_NAMES]
    return hashlib.sha256(repr(values).encode("utf-8")).hexdigest()[:16]


def _index_state() -> Tuple:
    # Which vector collection answers (and on which backend), and the active generation: switching either
    # changes the answers while the keyword-corpus file may stay the same.
    from rag_core import index_generation, vector_backend

    pointer = index_generation.active_pointer() or {}
    return (
        config.resolve_chroma_collection_name(),
        vector_backend.backend_name(),
        str(pointer.get("generation_id") or ""),
    )


def _profile_state(tenant_id: str, rag_profile_id: str) -> Tuple:
    # The files of the RAG profile (synonyms, question types, ...) and the query-preprocessing rule files
    # (global and of this tenant), each as path + mtime_ns + size; a missing file is its own state.
    from rag_core import profile_loader, query_preprocess

    profile = str(rag_profile_id or "").strip() or "default"
    if profile_loader.is_valid_rag_profile_id(profile):
        directory = profile_loader.active_profile_dir(profile)
        profile_files = [str(directory / filename) for filename, _root in profile_loader.JSON_FILES.values()]
        pinned = profile_loader.pin_state(profile)
    else:
        profile_files = []
        pinned = ""
    rule_files = query_preprocess._rule_files(tenant_id)
    return (
        profile,
        pinned,
        tuple(_file_state(path) for path in profile_files),
        tuple(_file_state(path) for path in rule_files),
    )


def build_key(
    question: str,
    *,
    top_k: int,
    max_context_chars: int,
    tenant_id: str = "default",
    rag_profile_id: str = "default",
) -> Tuple:
    tenant = str(tenant_id or "").strip() or "default"
    return (
        tenant,
        normalize_question_for_exact_match(question or ""),
        _corpus_state(),
        _index_state(),
        _profile_state(tenant, rag_profile_id),
        _pipeline_state(),
        _settings_state(),
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
