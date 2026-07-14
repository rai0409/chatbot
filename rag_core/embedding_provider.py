from __future__ import annotations

from functools import lru_cache
from typing import Dict, List, Protocol, Sequence

import config


LOCAL_PROVIDER = "local"
OPENAI_PROVIDER = "openai"
BGE_M3_PROVIDER = "bge_m3"
BGE_M3_MODEL_NAME = "BAAI/bge-m3"
RESERVED_PROVIDER_NAMES = {"qwen"}


class EmbeddingProvider(Protocol):
    name: str

    def embed_queries(self, queries: Sequence[str], *, client=None) -> List[List[float]]:
        ...

    def embed_documents(self, documents: Sequence[str], *, client=None) -> List[List[float]]:
        ...


def _load_sentence_transformer_class(provider_name: str):
    try:
        from sentence_transformers import SentenceTransformer
    except Exception as exc:
        raise RuntimeError(
            f"{provider_name} embeddings require sentence-transformers. "
            f"Install it and set EMBED_PROVIDER={provider_name}."
        ) from exc
    return SentenceTransformer


@lru_cache(maxsize=8)
def _get_sentence_transformer_model(model_name: str, provider_name: str):
    sentence_transformer = _load_sentence_transformer_class(provider_name)
    return sentence_transformer(model_name)


def _get_local_model(model_name: str):
    return _get_sentence_transformer_model(model_name, LOCAL_PROVIDER)


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
            "LOCAL_EMBED_MODEL", default="all-MiniLM-L6-v2"
        )

    def _embed(self, texts: Sequence[str]) -> List[List[float]]:
        model = _get_local_model(str(self.model_name))
        return model.encode(list(texts), normalize_embeddings=True).tolist()

    def embed_queries(self, queries: Sequence[str], *, client=None) -> List[List[float]]:
        return self._embed(queries)

    def embed_documents(self, documents: Sequence[str], *, client=None) -> List[List[float]]:
        return self._embed(documents)


class BgeM3EmbeddingProvider:
    name = BGE_M3_PROVIDER

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

    def __init__(self, model_name: str | None = None):
        self.model_name = model_name or config.getenv_first(
            "OPENAI_EMBED_MODEL", "EMBED_MODEL", default="text-embedding-3-small"
        )

    def _embed(self, texts: Sequence[str], *, client=None) -> List[List[float]]:
        if client is None:
            client = _create_openai_client()
        if client is None:
            raise RuntimeError("OpenAI client is required for remote embeddings")
        resp = client.embeddings.create(model=self.model_name, input=list(texts))
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
