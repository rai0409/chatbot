from __future__ import annotations

import hashlib
import os
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List, Protocol, Sequence

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


@load_once(maxsize=8)
def _get_sentence_transformer_model(model_name: str, provider_name: str):
    sentence_transformer = _load_sentence_transformer_class(provider_name)
    return sentence_transformer(model_name)


def _get_local_model(model_name: str):
    return _get_sentence_transformer_model(model_name, LOCAL_PROVIDER)


@load_once(maxsize=8)
def _get_external_local_model(model_path: str):
    sentence_transformer = _load_sentence_transformer_class(LOCAL_PROVIDER)
    return sentence_transformer(
        model_path,
        local_files_only=True,
        trust_remote_code=False,
    )


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
    return _get_sentence_transformer_model(model_name, BGE_M3_PROVIDER)


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
    base_url = config.getenv_first("OPENAI_BASE_URL", default=getattr(config, "OPENAI_BASE_URL", None))
    openai_class = _load_openai_class()
    if base_url:
        return openai_class(api_key=api_key, base_url=base_url)
    return openai_class(api_key=api_key)


class LocalEmbeddingProvider:
    name = LOCAL_PROVIDER

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

    def _embed(self, texts: Sequence[str]) -> List[List[float]]:
        if self.model_path:
            self.embedding_asset_metadata()
            model = _get_external_local_model(str(self.model_path))
        else:
            model = _get_local_model(str(self.model_name))
        return model.encode(list(texts), normalize_embeddings=True).tolist()

    def embed_queries(self, queries: Sequence[str], *, client=None) -> List[List[float]]:
        return self._embed(_with_prefix(queries, self.query_prefix))

    def embed_documents(self, documents: Sequence[str], *, client=None) -> List[List[float]]:
        return self._embed(_with_prefix(documents, self.document_prefix))


class BgeM3EmbeddingProvider:
    name = BGE_M3_PROVIDER
    prefix_scheme = PREFIX_SCHEME_NONE

    def __init__(self, model_name: str | None = None):
        self.model_name = model_name or BGE_M3_MODEL_NAME

    def _embed(self, texts: Sequence[str]) -> List[List[float]]:
        model = _get_bge_m3_model(str(self.model_name))
        return model.encode(list(texts), normalize_embeddings=True).tolist()

    def embed_queries(self, queries: Sequence[str], *, client=None) -> List[List[float]]:
        return self._embed(queries)

    def embed_documents(self, documents: Sequence[str], *, client=None) -> List[List[float]]:
        return self._embed(documents)


class OpenAIEmbeddingProvider:
    name = OPENAI_PROVIDER
    prefix_scheme = PREFIX_SCHEME_NONE

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
