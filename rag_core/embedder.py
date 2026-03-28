from __future__ import annotations

from functools import lru_cache
from typing import List, Sequence

import config


@lru_cache(maxsize=4)
def _get_local_model(model_name: str):
    try:
        from sentence_transformers import SentenceTransformer
    except Exception as exc:
        raise RuntimeError(
            "Local embeddings require sentence-transformers. Install it and set EMBED_PROVIDER=local."
        ) from exc
    return SentenceTransformer(model_name)


def embed_queries(queries: Sequence[str], client=None) -> List[List[float]]:
    provider = (
        config.getenv_first("EMBED_PROVIDER", default="openai") or "openai"
    ).lower()
    if provider == "local":
        model_name = config.getenv_first("LOCAL_EMBED_MODEL", default="all-MiniLM-L6-v2")
        model = _get_local_model(model_name)
        return model.encode(list(queries), normalize_embeddings=True).tolist()
    if client is None:
        raise RuntimeError("OpenAI client is required for remote embeddings")
    model_name = config.getenv_first(
        "OPENAI_EMBED_MODEL", "EMBED_MODEL", default="text-embedding-3-small"
    )
    resp = client.embeddings.create(model=model_name, input=list(queries))
    return [item.embedding for item in resp.data]
