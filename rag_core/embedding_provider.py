from __future__ import annotations

import hashlib
import inspect
import os
import threading
from collections import OrderedDict
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List, Optional, Protocol, Sequence

import config
from rag_core.model_cache import load_once
from scripts import validate_embedding_asset_contract


LOCAL_PROVIDER = "local"
OPENAI_PROVIDER = "openai"
BGE_M3_PROVIDER = "bge_m3"
BGE_M3_MODEL_NAME = "BAAI/bge-m3"
RESERVED_PROVIDER_NAMES = {"qwen"}

# Japanese-first default (cl-nagoya/ruri-v3-130m: 512 dims, max 8192 tokens,
# Apache-2.0). Collections stamped with another model fail the fingerprint
# check until they are re-ingested, which is the intended safety net.
DEFAULT_LOCAL_EMBED_MODEL = "cl-nagoya/ruri-v3-130m"

# ruri-v3 retrieval prefixes from its model card (trailing space included).
RURI_V3_QUERY_PREFIX = "検索クエリ: "
RURI_V3_DOCUMENT_PREFIX = "検索文書: "
PREFIX_SCHEME_NONE = "none"
PREFIX_SCHEME_RURI_V3 = "ruri-v3-retrieval"


class EmbeddingProvider(Protocol):
    name: str

    def embed_queries(self, queries: Sequence[str], *, client=None) -> List[List[float]]:
        ...

    def embed_documents(self, documents: Sequence[str], *, client=None) -> List[List[float]]:
        ...


def _model_default_prefixes(model_name: str) -> tuple[str, str]:
    name = str(model_name or "").lower()
    if "ruri-v3" in name and "reranker" not in name:
        return RURI_V3_QUERY_PREFIX, RURI_V3_DOCUMENT_PREFIX
    return "", ""


def _resolve_prefixes(model_name: str) -> tuple[str, str]:
    # os.environ (not getenv_first) so an explicitly empty value disables a
    # model default prefix. Quote values with trailing spaces in .env.
    query_default, document_default = _model_default_prefixes(model_name)
    query_prefix = os.environ.get("EMBED_QUERY_PREFIX")
    document_prefix = os.environ.get("EMBED_DOCUMENT_PREFIX")
    return (
        query_default if query_prefix is None else query_prefix,
        document_default if document_prefix is None else document_prefix,
    )


def prefix_scheme_name(query_prefix: str, document_prefix: str) -> str:
    if not query_prefix and not document_prefix:
        return PREFIX_SCHEME_NONE
    if (query_prefix, document_prefix) == (RURI_V3_QUERY_PREFIX, RURI_V3_DOCUMENT_PREFIX):
        return PREFIX_SCHEME_RURI_V3
    digest = hashlib.sha256(f"{query_prefix}\x00{document_prefix}".encode("utf-8")).hexdigest()[:8]
    return f"custom-{digest}"


def _with_prefix(texts: Sequence[str], prefix: str) -> List[str]:
    if not prefix:
        return list(texts)
    return [prefix + str(text) for text in texts]


def _load_sentence_transformer_class(provider_name: str):
    try:
        from sentence_transformers import SentenceTransformer
    except Exception as exc:
        raise RuntimeError(
            f"{provider_name} embeddings require sentence-transformers. "
            f"Install it and set EMBED_PROVIDER={provider_name}."
        ) from exc
    return SentenceTransformer


def local_model_revision() -> str:
    """LOCAL_EMBED_MODEL_REVISION: hub revision (commit hash / tag) the local model is pinned to; "" = latest."""
    return str(getattr(config, "LOCAL_EMBED_MODEL_REVISION", "") or "").strip()


def embed_max_seq_length() -> int:
    """EMBED_MAX_SEQ_LENGTH: truncation length in tokens; 0 = the model default."""
    try:
        return max(0, int(getattr(config, "EMBED_MAX_SEQ_LENGTH", 0) or 0))
    except (TypeError, ValueError):
        return 0


def embed_batch_size() -> int:
    try:
        return max(1, int(getattr(config, "EMBED_BATCH_SIZE", 32) or 32))
    except (TypeError, ValueError):
        return 32


def _apply_max_seq_length(model, max_seq_length: int) -> None:
    """EMBED_MAX_SEQ_LENGTH is part of the stamped profile, so a model that cannot take it must be visible."""
    if max_seq_length <= 0:
        return
    try:
        model.max_seq_length = int(max_seq_length)
        actual = model.max_seq_length
    except (AttributeError, TypeError, ValueError) as exc:
        raise ValueError("embedding model cannot apply or report max_seq_length") from exc
    if isinstance(actual, bool) or actual != max_seq_length:
        raise ValueError(f"embedding model reports max_seq_length={actual!r}, expected {max_seq_length}")


_THREADS_LOCK = threading.Lock()
_THREADS_APPLIED: Dict[str, Any] = {"value": None}


def apply_num_threads() -> Optional[int]:
    """torch.set_num_threads(EMBED_NUM_THREADS) once per value; 0 or no torch = untouched."""
    try:
        wanted = int(getattr(config, "EMBED_NUM_THREADS", 0) or 0)
    except (TypeError, ValueError):
        wanted = 0
    if wanted <= 0:
        return None
    with _THREADS_LOCK:
        if _THREADS_APPLIED["value"] == wanted:
            return wanted
        try:
            import torch  # noqa: WPS433 - optional, loaded with sentence-transformers anyway

            torch.set_num_threads(wanted)
        except ImportError:  # no torch: nothing to tune
            return None
        _THREADS_APPLIED["value"] = wanted
        return wanted


@load_once(maxsize=8)
def _get_sentence_transformer_model(model_name: str, provider_name: str, revision: str = "", max_seq_length: int = 0):
    # The cache key is every argument: a new revision or truncation length is a new model object.
    sentence_transformer = _load_sentence_transformer_class(provider_name)
    apply_num_threads()
    kwargs: Dict[str, Any] = {"trust_remote_code": False}
    if revision:
        kwargs["revision"] = revision
    model = sentence_transformer(model_name, **kwargs)
    _apply_max_seq_length(model, max_seq_length)
    return model


def _get_local_model(model_name: str):
    return _get_sentence_transformer_model(model_name, LOCAL_PROVIDER, local_model_revision(), embed_max_seq_length())


def _get_external_local_model(model_path: str):
    return _load_external_local_model(model_path, embed_max_seq_length())


_get_external_local_model.cache_clear = lambda: _load_external_local_model.cache_clear()  # type: ignore[attr-defined]


@load_once(maxsize=8)
def _load_external_local_model(model_path: str, max_seq_length: int = 0):
    sentence_transformer = _load_sentence_transformer_class(LOCAL_PROVIDER)
    apply_num_threads()
    model = sentence_transformer(
        model_path,
        local_files_only=True,
        trust_remote_code=False,
    )
    _apply_max_seq_length(model, max_seq_length)
    return model


def _accepts_batch_size(model) -> bool:
    encode = getattr(model, "encode", None)
    if encode is None:
        return False
    try:
        params = inspect.signature(encode).parameters
    except (TypeError, ValueError):
        return False
    return "batch_size" in params or any(p.kind is inspect.Parameter.VAR_KEYWORD for p in params.values())


def _encode(model, texts: Sequence[str]) -> List[List[float]]:
    texts = list(texts)
    if _accepts_batch_size(model):
        out = model.encode(texts, normalize_embeddings=True, batch_size=embed_batch_size())
    else:  # test doubles and old wrappers
        out = model.encode(texts, normalize_embeddings=True)
    return out.tolist() if hasattr(out, "tolist") else [list(v) for v in out]


_QUERY_CACHE_ATTR = "_kuraden_query_embedding_cache"
_QUERY_CACHE_LOCK = threading.Lock()


def _query_cache_size() -> int:
    try:
        return max(0, int(getattr(config, "EMBED_QUERY_CACHE_SIZE", 0) or 0))
    except (TypeError, ValueError):
        return 0


def _encode_queries_cached(model, prefixed: Sequence[str]) -> List[List[float]]:
    """LRU cache of query vectors kept on the loaded model object.

    The model object is unique per (model, revision, max length) in the loader
    cache and the texts already carry the query prefix, so the key is
    (embedding identity, prefixed text). Misses are encoded in one batch."""
    size = _query_cache_size()
    texts = list(prefixed)
    if size <= 0 or not texts:
        return _encode(model, texts)
    with _QUERY_CACHE_LOCK:
        cache = getattr(model, _QUERY_CACHE_ATTR, None)
        if cache is None:
            cache = OrderedDict()
            try:
                setattr(model, _QUERY_CACHE_ATTR, cache)
            except (AttributeError, TypeError):  # an object without attributes: no cache
                cache = None
        if cache is None:
            hits: Dict[str, List[float]] = {}
        else:
            hits = {}
            for text in texts:
                if text in cache:
                    cache.move_to_end(text)
                    hits[text] = cache[text]
    missing = list(dict.fromkeys(t for t in texts if t not in hits))
    if missing:
        vectors = _encode(model, missing)
        fresh = {t: list(v) for t, v in zip(missing, vectors)}
        hits.update(fresh)
        if cache is not None:
            with _QUERY_CACHE_LOCK:
                for text, vector in fresh.items():
                    cache[text] = vector
                    cache.move_to_end(text)
                while len(cache) > size:
                    cache.popitem(last=False)
    return [list(hits[t]) for t in texts]


@lru_cache(maxsize=8)
def validate_external_local_model_asset(
    model_name: str,
    model_path: str,
) -> dict[str, Any]:
    root = Path(__file__).resolve().parents[1]
    contract = root / "config/embedding_assets/retrieval_baseline.asset.json"
    source_contract = root / "config/embedding_assets/retrieval_baseline.source.json"
    try:
        result = validate_embedding_asset_contract.validate_contract(
            contract_path=contract,
            source_contract_path=source_contract,
            asset_dir=Path(model_path),
        )
    except validate_embedding_asset_contract.ContractError as exc:
        raise RuntimeError("external embedding asset validation failed") from exc

    if result["model_id"] != model_name:
        raise RuntimeError("external embedding asset model identity mismatch")
    return result


def _get_bge_m3_model(model_name: str = BGE_M3_MODEL_NAME):
    return _get_sentence_transformer_model(model_name, BGE_M3_PROVIDER, "", embed_max_seq_length())


def _load_openai_class():
    try:
        from openai import OpenAI
    except Exception as exc:
        raise RuntimeError("OpenAI SDK is required for OpenAI embeddings") from exc
    return OpenAI


def _create_openai_client():
    api_key = config.getenv_first("OPENAI_API_KEY", default=getattr(config, "OPENAI_API_KEY", ""))
    if api_key is None or str(api_key).strip() == "":
        raise RuntimeError("OPENAI_API_KEY is missing for OpenAI embeddings")
    # EMBEDDING_BASE_URL (config.py) lets the embeddings use another endpoint than the LLM; empty keeps OPENAI_BASE_URL.
    base_url = (
        str(getattr(config, "EMBEDDING_BASE_URL", "") or "").strip()
        or config.getenv_first("OPENAI_BASE_URL", default=getattr(config, "OPENAI_BASE_URL", None))
    )
    openai_class = _load_openai_class()
    if base_url:
        return openai_class(api_key=api_key, base_url=base_url)
    return openai_class(api_key=api_key)


class LocalEmbeddingProvider:
    name = LOCAL_PROVIDER
    normalize_embeddings = True
    distance = "cosine"

    def __init__(self, model_name: str | None = None):
        self.model_name = model_name or config.getenv_first(
            "LOCAL_EMBED_MODEL", default=DEFAULT_LOCAL_EMBED_MODEL
        )
        self.model_path = config.getenv_first("LOCAL_EMBED_MODEL_PATH", default=None)
        # Prefixes follow the canonical model identity, not the asset path.
        self.query_prefix, self.document_prefix = _resolve_prefixes(str(self.model_name))
        self.prefix_scheme = prefix_scheme_name(self.query_prefix, self.document_prefix)

    def embedding_asset_metadata(self) -> dict[str, Any] | None:
        if not self.model_path:
            return None
        return validate_external_local_model_asset(
            str(self.model_name),
            str(self.model_path),
        )

    @property
    def revision(self) -> str:
        # An external asset path is pinned by its asset contract, not by a hub revision.
        return "" if self.model_path else local_model_revision()

    @property
    def max_seq_length(self) -> int:
        return embed_max_seq_length()

    def _model(self):
        if self.model_path:
            self.embedding_asset_metadata()
            return _get_external_local_model(str(self.model_path))
        return _get_local_model(str(self.model_name))

    def _embed(self, texts: Sequence[str]) -> List[List[float]]:
        return _encode(self._model(), texts)

    def embed_queries(self, queries: Sequence[str], *, client=None) -> List[List[float]]:
        return _encode_queries_cached(self._model(), _with_prefix(queries, self.query_prefix))

    def embed_documents(self, documents: Sequence[str], *, client=None) -> List[List[float]]:
        return self._embed(_with_prefix(documents, self.document_prefix))


class BgeM3EmbeddingProvider:
    name = BGE_M3_PROVIDER
    prefix_scheme = PREFIX_SCHEME_NONE
    normalize_embeddings = True
    distance = "cosine"

    def __init__(self, model_name: str | None = None):
        self.model_name = model_name or BGE_M3_MODEL_NAME

    revision = ""

    @property
    def max_seq_length(self) -> int:
        return embed_max_seq_length()

    def _embed(self, texts: Sequence[str]) -> List[List[float]]:
        model = _get_bge_m3_model(str(self.model_name))
        return _encode(model, texts)

    def embed_queries(self, queries: Sequence[str], *, client=None) -> List[List[float]]:
        return _encode_queries_cached(_get_bge_m3_model(str(self.model_name)), queries)

    def embed_documents(self, documents: Sequence[str], *, client=None) -> List[List[float]]:
        return self._embed(documents)


class OpenAIEmbeddingProvider:
    name = OPENAI_PROVIDER
    prefix_scheme = PREFIX_SCHEME_NONE
    normalize_embeddings = True
    distance = "cosine"
    revision = ""
    max_seq_length = 0

    def __init__(self, model_name: str | None = None):
        self.model_name = model_name or config.getenv_first(
            "OPENAI_EMBED_MODEL", "EMBED_MODEL", default="text-embedding-3-small"
        )

    def _embed(self, texts: Sequence[str], *, client=None) -> List[List[float]]:
        if client is None:
            client = _create_openai_client()
        if client is None:
            raise RuntimeError("OpenAI client is required for remote embeddings")
        # Stage 14: never wait forever for the embedding API (OpenAI SDK
        # with_options; clients without it are used as they are).
        timeout = float(getattr(config, "OPENAI_EMBED_TIMEOUT_SECONDS", 30.0) or 30.0)
        with_options = getattr(client, "with_options", None)
        api = with_options(timeout=timeout) if callable(with_options) else client
        resp = api.embeddings.create(model=self.model_name, input=list(texts))
        return [item.embedding for item in resp.data]

    def embed_queries(self, queries: Sequence[str], *, client=None) -> List[List[float]]:
        return self._embed(queries, client=client)

    def embed_documents(self, documents: Sequence[str], *, client=None) -> List[List[float]]:
        return self._embed(documents, client=client)


def default_provider_name() -> str:
    return (config.getenv_first("EMBED_PROVIDER", default=LOCAL_PROVIDER) or LOCAL_PROVIDER).lower()


def get_embedding_provider(provider_name: str | None = None) -> EmbeddingProvider:
    name = (provider_name or default_provider_name()).strip().lower()
    if name == LOCAL_PROVIDER:
        return LocalEmbeddingProvider()
    if name == BGE_M3_PROVIDER:
        return BgeM3EmbeddingProvider()
    if name == OPENAI_PROVIDER:
        return OpenAIEmbeddingProvider()
    if name in RESERVED_PROVIDER_NAMES:
        raise NotImplementedError(f"Embedding provider '{name}' is reserved but not implemented yet")
    supported = ", ".join(
        sorted({LOCAL_PROVIDER, BGE_M3_PROVIDER, OPENAI_PROVIDER, *RESERVED_PROVIDER_NAMES})
    )
    raise ValueError(f"Unknown embedding provider '{name}'. Supported or reserved providers: {supported}")


def is_local_provider(provider_name: str | None = None) -> bool:
    return (provider_name or default_provider_name()).strip().lower() == LOCAL_PROVIDER


def active_fingerprint(provider_name: str | None = None) -> Dict[str, str]:
    provider = get_embedding_provider(provider_name)
    return {
        "embed_provider": str(provider.name),
        "embed_model": str(getattr(provider, "model_name", "") or ""),
    }


def active_prefix_scheme(provider_name: str | None = None) -> str:
    provider = get_embedding_provider(provider_name)
    return str(getattr(provider, "prefix_scheme", PREFIX_SCHEME_NONE) or PREFIX_SCHEME_NONE)


# Facts of the code that decide whether stored vectors and query vectors are comparable.
# 2026-11-22: the loaders pin LOCAL_EMBED_MODEL_REVISION and apply EMBED_MAX_SEQ_LENGTH,
# so both are part of the profile (stamped at ingest, compared when stamped).
PROFILE_VERSION = "1"
PROFILE_KEYS = ("embed_normalize", "embed_distance", "profile_version", "embed_revision", "embed_max_seq_length")
# Optional profile keys are written only when they differ from these defaults,
# so collections stamped before they existed (or with the defaults) still match.
PROFILE_OPTIONAL_DEFAULTS = {"embed_revision": "", "embed_max_seq_length": "0"}


def provider_profile(provider) -> Dict[str, str]:
    profile = {
        "embed_normalize": "true" if getattr(provider, "normalize_embeddings", True) else "false",
        "embed_distance": str(getattr(provider, "distance", "cosine") or "cosine"),
        "profile_version": PROFILE_VERSION,
    }
    revision = str(getattr(provider, "revision", "") or "")
    max_seq = str(int(getattr(provider, "max_seq_length", 0) or 0))
    if revision != PROFILE_OPTIONAL_DEFAULTS["embed_revision"]:
        profile["embed_revision"] = revision
    if max_seq != PROFILE_OPTIONAL_DEFAULTS["embed_max_seq_length"]:
        profile["embed_max_seq_length"] = max_seq
    return profile


def profile_value(profile: Dict[str, str], key: str) -> str:
    """A profile value with the default of an optional key filled in."""
    return str(profile.get(key, PROFILE_OPTIONAL_DEFAULTS.get(key, "")))


def loaded_embedding_dim(provider=None) -> Optional[int]:
    """The vector size of the active local model when it is already loaded (never loads it); else None."""
    provider = provider or get_embedding_provider()
    model = None
    if isinstance(provider, LocalEmbeddingProvider) and not provider.model_path:
        model = _get_sentence_transformer_model.peek(  # type: ignore[attr-defined]
            str(provider.model_name), LOCAL_PROVIDER, local_model_revision(), embed_max_seq_length()
        )
    if model is None:
        return None
    getter = getattr(model, "get_sentence_embedding_dimension", None)
    try:
        return int(getter()) if callable(getter) else None
    except (TypeError, ValueError, AttributeError):
        return None


def embedding_identity(provider_name: str | None = None, *, embedding_dim: Optional[int] = None) -> Dict[str, Any]:
    """What a calibrated distance threshold depends on: provider, model, revision,
    prefix scheme, vector size and truncation length."""
    provider = get_embedding_provider(provider_name)
    dim = embedding_dim if embedding_dim is not None else loaded_embedding_dim(provider)
    return {
        "provider": str(provider.name),
        "model": str(getattr(provider, "model_name", "") or ""),
        "revision": str(getattr(provider, "revision", "") or ""),
        "prefix_scheme": str(getattr(provider, "prefix_scheme", PREFIX_SCHEME_NONE) or PREFIX_SCHEME_NONE),
        "dim": int(dim) if dim else None,
        "max_seq_length": int(getattr(provider, "max_seq_length", 0) or 0),
    }


def active_profile(provider_name: str | None = None) -> Dict[str, str]:
    return provider_profile(get_embedding_provider(provider_name))


def embed_queries(
    queries: Sequence[str],
    *,
    client=None,
    provider_name: str | None = None,
) -> List[List[float]]:
    provider = get_embedding_provider(provider_name)
    return provider.embed_queries(queries, client=client)


def embed_documents(
    documents: Sequence[str],
    *,
    client=None,
    provider_name: str | None = None,
) -> List[List[float]]:
    provider = get_embedding_provider(provider_name)
    return provider.embed_documents(documents, client=client)
