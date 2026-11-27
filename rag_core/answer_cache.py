from __future__ import annotations

import hashlib
import os
import re
import time
from collections import OrderedDict
from threading import Lock
from typing import Any, Dict, Optional, Tuple

import config
from rag_core.question_normalization import fold_long_vowels, normalize_question_for_exact_match


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


# --- state memo ---------------------------------------------------------------------------------------------------
# build_key stats a dozen files and digests the settings on every request. ANSWER_CACHE_STATE_MEMO_MS keeps each
# state part for that many milliseconds (default 1000 with APP_ENV=production, 0 = off otherwise, so tests and
# development see a changed file at once). A change is then picked up at most that late; answers built in that
# window are keyed by the previous state, like a request that started just before the change.
_MEMO_LOCK = Lock()
_MEMO: Dict[Tuple, Tuple[float, Any]] = {}


def _state_memo_seconds() -> float:
    raw = str(os.getenv("ANSWER_CACHE_STATE_MEMO_MS", "")).strip()
    if raw:
        try:
            return max(0.0, float(raw) / 1000.0)
        except ValueError:
            return 0.0
    return 1.0 if str(os.getenv("APP_ENV", "")).strip().lower() == "production" else 0.0


def _memo(name: Tuple, compute):
    ttl = _state_memo_seconds()
    if ttl <= 0:
        return compute()
    now = time.monotonic()
    with _MEMO_LOCK:
        entry = _MEMO.get(name)
        if entry is not None and now < entry[0]:
            return entry[1]
    value = compute()
    with _MEMO_LOCK:
        _MEMO[name] = (now + ttl, value)
        if len(_MEMO) > 1024:  # tenants x profiles: bounded
            _MEMO.clear()
    return value


def clear_state_memo() -> None:
    with _MEMO_LOCK:
        _MEMO.clear()


def _corpus_state() -> Tuple:
    return _memo(("corpus",), _corpus_state_uncached)


def _corpus_state_uncached() -> Tuple:
    # NOTE (per-tenant invalidation): the served keyword corpus is one file for every tenant, and neither
    # index_manifest nor index_generation keeps a per-tenant identity, so an ingest for any tenant changes every
    # tenant's key. When a cheap per-tenant corpus digest exists (e.g. a per-tenant row count/checksum in the
    # index manifest), key on it here instead of the file state.
    from rag_core import index_generation

    chunks_state = _file_state(index_generation.served_keyword_path())
    if getattr(config, "APPROVED_QA_ENABLED", False):
        approved_state = _file_state(str(getattr(config, "APPROVED_QA_PATH", "") or ""))
    else:
        approved_state = ("approved_qa_disabled",)
    return (chunks_state, approved_state)


def _pipeline_state() -> Tuple:
    return _memo(("pipeline",), _pipeline_state_uncached)


def _pipeline_state_uncached() -> Tuple:
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
        # Dimension affects the guard only when a calibration file is configured.
        # embedding_identity peeks at an already loaded model for that case.
        calibrated = bool(str(getattr(config, "RAG_DIST_CALIBRATION_FILE", "") or "").strip())
        embed = embedding_provider.embedding_identity(embedding_dim=None if calibrated else 0)
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
        # config.query_preprocess_enabled(): the module attribute, else the environment, else the default.
        bool(config.query_preprocess_enabled()),
        # The tenant/product policy of /chat (top_k clamp, LLM allowed, approved answers allowed).
        bool(config.CHAT_USE_TENANT_PROFILE),
        # Which model answers may change with the model profiles file (rag_core/llm_profiles.py); empty without one.
        _profiles_fingerprint(),
        _config_files_state(),
    )


def _config_files_state() -> Tuple:
    """Files that change an answer without being corpus, profile or settings: the prompt templates, the grounded
    domain rules, and the product tenant/profile files (the /chat answer policy)."""
    from pathlib import Path

    from rag_core.japanese_normalizer import DEFAULT_SYNONYM_CONFIG

    base = Path(config.BASE_DIR)
    paths = sorted(str(p) for p in (base / "prompts").glob("*.jinja"))
    domain_rules = str(os.getenv("GROUNDED_DOMAIN_RULES_PATH", "") or "").strip()
    paths.append(domain_rules or str(base / "configs" / "grounded_domain_rules.json"))
    try:
        from rag_core import product_profile, tenant_profile

        for directory in (tenant_profile.TENANT_PROFILE_DIR, product_profile.PRODUCT_PROFILE_DIR):
            paths.extend(sorted(str(p) for p in Path(directory).glob("*.json")))
    except (ImportError, AttributeError, OSError):  # the cache key never breaks an answer
        paths.append("product_policy_unknown")
    calibration = str(getattr(config, "RAG_DIST_CALIBRATION_FILE", "") or "").strip()
    if calibration:
        paths.append(str(base / calibration))
    paths.append(str(DEFAULT_SYNONYM_CONFIG))
    return tuple(_file_state(path) for path in paths)


def _profiles_fingerprint() -> str:
    try:
        from rag_core import llm_profiles

        # The file, and the tiers assigned in the console: both change which model answers.
        return llm_profiles.fingerprint() + ":" + llm_profiles.assignment_digest()
    except Exception:  # noqa: BLE001 - the cache key never breaks an answer
        return "unknown"


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
    "QUERY_ROUTING_ENABLED", "SYNONYM_LANE_ENABLED", "SYNONYM_LANE_MAX_VARIANTS",
    "CROSS_ENCODER_REVISION", "CROSS_ENCODER_BATCH_SIZE", "CROSS_ENCODER_TIMEOUT_MS",
    "CROSS_ENCODER_BOOST_WEIGHT", "LOCAL_EMBED_MODEL_REVISION", "EMBED_MAX_SEQ_LENGTH",
    "STAGING_GUARD_LENIENT", "RAG_DIST_CALIBRATED_MODEL", "KEYWORD_NUMERIC_TOKENS",
    "DISABLE_GUARD", "RAG_MIN_KEYWORD_EVIDENCE_BM25",
    "BM25_K1", "BM25_B", "IGNORE_SEARCHABLE", "TENANT_FILTER_STRICT",
    "LLM_ENABLED", "LLM_TENANT_ALLOWLIST", "LLM_TENANT_DENYLIST", "LLM_FEATURES", "LLM_FEATURE_MAX_TOKENS",
    "GROUNDING_VERIFY_ENABLED", "GROUNDING_MIN_COVERAGE",
    "RAG_HARD_MAX_DIST", "RAG_HARD_MAX_DIST_PROCEDURE_DELTA", "RAG_SOFT_DIST_OTHER",
    "RAG_SOFT_DIST_RESET", "RAG_SOFT_DIST_CHANGE", "RAG_SOFT_DIST_PROCEDURE",
    "CROSS_ENCODER_TOP_N", "CROSS_ENCODER_MAX_LENGTH", "CROSS_ENCODER_SKIP_MARGIN",
    "OPENAI_BASE_URL", "CHAT_COMPLETION_MAX_TOKENS",
)
_SETTING_ENV_NAMES = (
    "QUERY_SYNONYM_EXPANSION", "QUERY_TYPO_CORRECTION",
    "INTENT_RULES", "COMPOSE_QUERY_MODE", "PREFER_SORT_MODE", "EXTRACTIVE_TERM_MODE",
    "EXTRACTIVE_EVIDENCE_RULE", "STAGED_RETRIEVAL_MARGIN", "QUERY_PREPROCESS_ENABLED", "GUARD_TERM_COVERAGE_MIN", "GUARD_YEAR_MISMATCH_ENABLED",
)


def _settings_state() -> str:
    return _memo(("settings",), _settings_state_uncached)


def _settings_state_uncached() -> str:
    values = [(name, repr(getattr(config, name, None))) for name in _SETTING_NAMES]
    # a config attribute (set by an evaluation run) wins over the environment, as in qa._setting
    values += [(name, str(getattr(config, name)) if getattr(config, name, None) is not None else os.getenv(name, ""))
               for name in _SETTING_ENV_NAMES]
    values.extend((name, os.getenv(name, "")) for name in
                  ("READING_LANE_ENABLED", "READING_LANE_MIN_BM25", "RAG_MAX_DISTANCE", *config.GUARD_THRESHOLD_NAMES))
    return hashlib.sha256(repr(values).encode("utf-8")).hexdigest()[:16]


def _index_state() -> Tuple:
    return _memo(("index",), _index_state_uncached)


def _index_state_uncached() -> Tuple:
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
    return _memo(("profile", tenant_id, rag_profile_id), lambda: _profile_state_uncached(tenant_id, rag_profile_id))


def _profile_state_uncached(tenant_id: str, rag_profile_id: str) -> Tuple:
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


def _cache_question_key(question: str) -> str:
    """Keep the original normalized question when cache equivalence is unproven.

    Only the optional katakana long vowel is folded (サーバ / サーバー, ユーザ / ユーザー share one key; the rule
    of question_normalization.exact_match_key). Colloquial variants (教えて / 教えてほしいんだけど) still get
    separate keys: sharing them needs proof that retrieval query, intent and question type are equal."""
    return fold_long_vowels(normalize_question_for_exact_match(question))


def build_key(
    question: str,
    *,
    top_k: int,
    max_context_chars: int,
    tenant_id: str = "default",
    rag_profile_id: str = "default",
    principal_digest: str = "",
) -> Tuple:
    """The cache key of one answer. The tenant is the first element (clear_tenant relies on it).

    principal_digest: a digest of the caller's access scope (groups/ACL principals). It MUST be passed as soon as
    a search is restricted per user (ACL-scoped corpora); otherwise one user's answer, built from documents only
    they may read, is served to another user of the tenant. Empty = the whole tenant sees the same corpus."""
    tenant = str(tenant_id or "").strip() or "default"
    return (
        tenant,
        _cache_question_key(question or ""),
        _corpus_state(),
        _index_state(),
        _profile_state(tenant, rag_profile_id),
        _pipeline_state(),
        _settings_state(),
        int(top_k),
        int(max_context_chars),
        str(principal_digest or ""),
    )


def _redis_backend() -> bool:
    return str(os.getenv("ANSWER_CACHE_BACKEND", "local")).strip().lower() == "redis"


_REDIS_TENANT_SAFE = re.compile(r"[^A-Za-z0-9_.-]")


def _redis_tenant(tenant_id: Any) -> str:
    # Only used to group keys for clear_tenant (the digest holds the full key, tenant included), so folding
    # unusual characters cannot make two tenants share an entry; it can only make clear_tenant clear both.
    return _REDIS_TENANT_SAFE.sub("_", str(tenant_id or "").strip() or "default")[:128]


def _redis_key(key: Tuple) -> str:
    tenant = key[0] if key else "default"
    return "ac:" + _redis_tenant(tenant) + ":" + hashlib.sha256(repr(key).encode("utf-8")).hexdigest()


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
    clear_state_memo()


def clear_tenant(tenant_id: str) -> int:
    """Remove the cached answers of one tenant (local and Redis). Returns how many local entries were removed.

    Called when a tenant's approved QA, documents or profiles change, so the next question is answered fresh
    instead of waiting for the key's file states (or the TTL) to move on."""
    tenant = str(tenant_id or "").strip() or "default"
    if _redis_backend():
        from webapi import state_backend

        try:
            client = state_backend.redis_client()
            batch = []
            for key in client.scan_iter(match="ac:" + _redis_tenant(tenant) + ":*", count=500):
                batch.append(key)
                if len(batch) >= 500:
                    client.delete(*batch)
                    batch = []
            if batch:
                client.delete(*batch)
        except state_backend.redis_errors() as exc:
            state_backend.warn_degraded("redis_answer_cache", exc)
    with _LOCK:
        doomed = [key for key in _CACHE if key and key[0] == tenant]
        for key in doomed:
            _CACHE.pop(key, None)
    clear_state_memo()
    return len(doomed)
