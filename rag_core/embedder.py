from __future__ import annotations

from typing import List, Sequence

from rag_core import embedding_provider


_get_local_model = embedding_provider._get_local_model


def embed_queries(
    queries: Sequence[str],
    client=None,
    provider_name: str | None = None,
) -> List[List[float]]:
    return embedding_provider.embed_queries(
        queries,
        client=client,
        provider_name=provider_name,
    )


def embed_documents(
    documents: Sequence[str],
    client=None,
    provider_name: str | None = None,
) -> List[List[float]]:
    return embedding_provider.embed_documents(
        documents,
        client=client,
        provider_name=provider_name,
    )


def get_embedding_provider(provider_name: str | None = None) -> embedding_provider.EmbeddingProvider:
    return embedding_provider.get_embedding_provider(provider_name)


def is_local_provider(provider_name: str | None = None) -> bool:
    return embedding_provider.is_local_provider(provider_name)
