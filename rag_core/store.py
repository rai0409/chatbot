from __future__ import annotations

from typing import Optional

import chromadb

import config


def get_vectorstore(collection_name: Optional[str] = None):
    client = chromadb.PersistentClient(path=config.VECTORSTORE_DIR)
    name = (
        collection_name
        or config.getenv_first("CHROMA_COLLECTION")
        or config.VECTORSTORE_COLLECTION_NAME
        or "rag_chunks"
    )
    return client.get_or_create_collection(
        name=name, metadata={"hnsw:space": "cosine"}
    )
