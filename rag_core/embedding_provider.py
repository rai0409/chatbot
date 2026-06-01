from __future__ import annotations

from functools import lru_cache
from typing import List, Protocol, Sequence

import config


LOCAL_PROVIDER = "local"
OPENAI_PROVIDER = "openai"
RESERVED_PROVIDER_NAMES = {"bge_m3", "qwen"}


class EmbeddingProvider(Protocol):
    name: str

    def embed_queries(self, queries: Sequence[str], *, client=None) -> List[List[float]]:
        ...


@lru_cache(maxsize=4)
def _get_local_model(model_name: str):
    try:
        from sentence_transformers import SentenceTransformer
    except Exception as exc:
        raise RuntimeError(
            "Local embeddings require sentence-transformers. Install it and set EMBED_PROVIDER=local."
        ) from exc
    return SentenceTransformer(model_name)


class LocalEmbeddingProvider:
    name = LOCAL_PROVIDER

    def __init__(self, model_name: str | None = None):
        self.model_name = model_name or config.getenv_first(
            "LOCAL_EMBED_MODEL", default="all-MiniLM-L6-v2"
        )

    def embed_queries(self, queries: Sequence[str], *, client=None) -> List[List[float]]:
        model = _get_local_model(str(self.model_name))
        return model.encode(list(queries), normalize_embeddings=True).tolist()


class OpenAIEmbeddingProvider:
    name = OPENAI_PROVIDER

    def __init__(self, model_name: str | None = None):
        self.model_name = model_name or config.getenv_first(
            "OPENAI_EMBED_MODEL", "EMBED_MODEL", default="text-embedding-3-small"
        )

    def embed_queries(self, queries: Sequence[str], *, client=None) -> List[List[float]]:
        if client is None:
            raise RuntimeError("OpenAI client is required for remote embeddings")
        resp = client.embeddings.create(model=self.model_name, input=list(queries))
        return [item.embedding for item in resp.data]


def default_provider_name() -> str:
    return (config.getenv_first("EMBED_PROVIDER", default=LOCAL_PROVIDER) or LOCAL_PROVIDER).lower()


def get_embedding_provider(provider_name: str | None = None) -> EmbeddingProvider:
    name = (provider_name or default_provider_name()).strip().lower()
    if name == LOCAL_PROVIDER:
        return LocalEmbeddingProvider()
    if name == OPENAI_PROVIDER:
        return OpenAIEmbeddingProvider()
    if name in RESERVED_PROVIDER_NAMES:
        raise NotImplementedError(f"Embedding provider '{name}' is reserved but not implemented yet")
    supported = ", ".join(sorted({LOCAL_PROVIDER, OPENAI_PROVIDER, *RESERVED_PROVIDER_NAMES}))
    raise ValueError(f"Unknown embedding provider '{name}'. Supported or reserved providers: {supported}")


def is_local_provider(provider_name: str | None = None) -> bool:
    return (provider_name or default_provider_name()).strip().lower() == LOCAL_PROVIDER


def embed_queries(
    queries: Sequence[str],
    *,
    client=None,
    provider_name: str | None = None,
) -> List[List[float]]:
    provider = get_embedding_provider(provider_name)
    return provider.embed_queries(queries, client=client)
