from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

import config
from rag_core import retrieval, store


# Captured at import time, before any monkeypatch, so the synthetic tests can
# assert they never use the production/default collection name.
_PROD_COLLECTION_NAME = config.VECTORSTORE_COLLECTION_NAME
_FORBIDDEN_COLLECTIONS = {_PROD_COLLECTION_NAME, "chatbot_chunks_v1", "rag_chunks"}

# Explicit non-production collection used by every test in this file.
NONPROD_COLLECTION = "pilot_persist_check_v1"

# Deterministic synthetic embeddings (3-dim), no model download, no network.
_RECORDS = [
    {"id": "alpha-1", "tenant_id": "tenant_alpha", "text": "alpha policy one", "embedding": [1.0, 0.0, 0.0]},
    {"id": "alpha-2", "tenant_id": "tenant_alpha", "text": "alpha policy two", "embedding": [0.9, 0.1, 0.0]},
    {"id": "beta-1", "tenant_id": "tenant_beta", "text": "beta manual one", "embedding": [0.0, 1.0, 0.0]},
    {"id": "beta-2", "tenant_id": "tenant_beta", "text": "beta manual two", "embedding": [0.0, 0.9, 0.1]},
]
_ALPHA_IDS = {"alpha-1", "alpha-2"}
_BETA_IDS = {"beta-1", "beta-2"}
_ALPHA_QUERY_EMBEDDING = [1.0, 0.0, 0.0]
_BETA_QUERY_EMBEDDING = [0.0, 1.0, 0.0]


def _point_store_at(monkeypatch, vectorstore_dir: Path) -> None:
    # Route ALL store access at the synthetic, non-production store before any
    # get_vectorstore() call, so the production/default vectorstore is never
    # opened or mutated.
    monkeypatch.setenv("EMBED_PROVIDER", "local")
    monkeypatch.setenv("LOCAL_EMBED_MODEL", "test-local-model")
    monkeypatch.delenv("CHROMA_COLLECTION", raising=False)
    monkeypatch.setattr(config, "VECTORSTORE_DIR", str(vectorstore_dir))
    monkeypatch.setattr(config, "VECTORSTORE_COLLECTION_NAME", NONPROD_COLLECTION)
    # The installed chromadb rejects a multi-key `where` ({searchable, tenant_id})
    # unless wrapped in $and. We do not change _build_base_where (retrieval /
    # isolation semantics are out of scope here); IGNORE_SEARCHABLE is an
    # existing config flag that yields a single-key tenant `where`, so the
    # tenant_id filter + _tenant_matches post-filter isolation path is exercised
    # against real chroma. See docs/reports for the chroma where-clause note.
    monkeypatch.setattr(config, "IGNORE_SEARCHABLE", True)
    store.reset_vectorstore_clients()


def _assert_non_production(vectorstore_dir: Path) -> None:
    assert NONPROD_COLLECTION not in _FORBIDDEN_COLLECTIONS
    assert config.VECTORSTORE_COLLECTION_NAME == NONPROD_COLLECTION
    # The active store dir must be the synthetic temp dir, never the repo's.
    assert str(vectorstore_dir) == config.VECTORSTORE_DIR
    repo_vectorstore = (Path(config.BASE_DIR) / "vectorstore").resolve()
    assert Path(config.VECTORSTORE_DIR).resolve() != repo_vectorstore


def _build_synthetic_store(monkeypatch, vectorstore_dir: Path) -> dict:
    _point_store_at(monkeypatch, vectorstore_dir)
    _assert_non_production(vectorstore_dir)

    collection = store.get_vectorstore(verify_embedding_fingerprint=False)
    assert collection.name == NONPROD_COLLECTION
    fingerprint = store.stamp_collection_fingerprint(collection)
    collection.upsert(
        ids=[r["id"] for r in _RECORDS],
        documents=[r["text"] for r in _RECORDS],
        embeddings=[r["embedding"] for r in _RECORDS],
        metadatas=[
            {"id": r["id"], "tenant_id": r["tenant_id"], "searchable": 1, "type": "chunk"}
            for r in _RECORDS
        ],
    )
    # Drop the client so on-disk state is flushed and the next open is a fresh
    # PersistentClient reading from disk (simulates a process restart).
    store.reset_vectorstore_clients()
    return fingerprint


def _retrieved_ids(tenant_id: str, query_embedding) -> set:
    hits = retrieval.vector_retrieve(
        "synthetic query",
        client=None,
        top_k=10,
        query_embedding=query_embedding,
        tenant_id=tenant_id,
    )
    return {str(h.metadata.get("id")) for h in hits}


def _assert_tenant_isolation() -> None:
    alpha_ids = _retrieved_ids("tenant_alpha", _ALPHA_QUERY_EMBEDDING)
    beta_ids = _retrieved_ids("tenant_beta", _BETA_QUERY_EMBEDDING)
    # Each tenant sees only its own chunks, and at least one of them.
    assert alpha_ids and alpha_ids <= _ALPHA_IDS
    assert not (alpha_ids & _BETA_IDS)
    assert beta_ids and beta_ids <= _BETA_IDS
    assert not (beta_ids & _ALPHA_IDS)
    # Cross-tenant query embedding similarity must not leak the other tenant:
    # query with beta's embedding but as tenant_alpha -> still alpha-only.
    cross = _retrieved_ids("tenant_alpha", _BETA_QUERY_EMBEDDING)
    assert not (cross & _BETA_IDS)


def _assert_fingerprint_valid(expected: dict) -> None:
    collection = store.get_vectorstore(verify_embedding_fingerprint=False)
    stamped = store.collection_fingerprint(collection)
    assert stamped == expected
    # Must not raise: stamped fingerprint matches the active provider/model.
    store.verify_collection_fingerprint(collection)


def _record_count() -> int:
    collection = store.get_vectorstore(verify_embedding_fingerprint=False)
    return collection.count()


# --- Reload durability + tenant isolation ----------------------------------


def test_reload_preserves_records_and_fingerprint(monkeypatch, tmp_path):
    vectorstore_dir = tmp_path / "vectorstore"
    fingerprint = _build_synthetic_store(monkeypatch, vectorstore_dir)

    # Fresh client (post reset) reads from disk.
    assert _record_count() == len(_RECORDS)
    _assert_fingerprint_valid(fingerprint)
    store.reset_vectorstore_clients()


def test_reload_preserves_tenant_isolation(monkeypatch, tmp_path):
    vectorstore_dir = tmp_path / "vectorstore"
    _build_synthetic_store(monkeypatch, vectorstore_dir)

    _assert_tenant_isolation()
    store.reset_vectorstore_clients()


# --- Backup/restore durability + tenant isolation --------------------------


def _run(cmd: list[str]) -> str:
    proc = subprocess.run(cmd, capture_output=True, text=True)
    assert proc.returncode == 0, f"command failed: {cmd}\nstdout={proc.stdout}\nstderr={proc.stderr}"
    return proc.stdout


def test_backup_restore_preserves_records_fingerprint_and_isolation(monkeypatch, tmp_path):
    src = tmp_path / "src"
    vectorstore_dir = src / "vectorstore"
    fingerprint = _build_synthetic_store(monkeypatch, vectorstore_dir)
    repo = Path(config.BASE_DIR)

    # Backup the synthetic source (read-only on source), into a temp dir.
    backups_dir = tmp_path / "backups"
    _run([
        "bash", str(repo / "scripts" / "backup.sh"),
        "--source-dir", str(src),
        "--output-dir", str(backups_dir),
    ])
    archives = sorted(backups_dir.glob("chatbot_backup_*.tar.gz"))
    assert len(archives) == 1, archives

    # Restore into a SEPARATE temp target (staging, non-destructive, hash
    # verified). restore.sh exits non-zero on any manifest mismatch.
    restore_target = tmp_path / "restored"
    out = _run([
        "bash", str(repo / "scripts" / "restore.sh"),
        str(archives[0]),
        "--target", str(restore_target),
    ])
    assert "restore verified" in out

    # Point the store at the restored copy and re-assert everything.
    restored_vectorstore = restore_target / "vectorstore"
    assert restored_vectorstore.is_dir()
    monkeypatch.setattr(config, "VECTORSTORE_DIR", str(restored_vectorstore))
    store.reset_vectorstore_clients()

    assert _record_count() == len(_RECORDS)
    _assert_fingerprint_valid(fingerprint)
    _assert_tenant_isolation()

    # The restore never touched the repo's production vectorstore.
    repo_vectorstore = (repo / "vectorstore").resolve()
    assert restored_vectorstore.resolve() != repo_vectorstore
    store.reset_vectorstore_clients()


# --- Safety asserts ---------------------------------------------------------


def test_uses_non_production_collection_and_temp_dir(monkeypatch, tmp_path):
    vectorstore_dir = tmp_path / "vectorstore"
    _point_store_at(monkeypatch, vectorstore_dir)
    _assert_non_production(vectorstore_dir)
    assert NONPROD_COLLECTION != _PROD_COLLECTION_NAME
    store.reset_vectorstore_clients()


def test_no_secrets_or_raw_keys_in_outputs(monkeypatch, tmp_path):
    vectorstore_dir = tmp_path / "vectorstore"
    fingerprint = _build_synthetic_store(monkeypatch, vectorstore_dir)
    collection = store.get_vectorstore(verify_embedding_fingerprint=False)
    blob = json.dumps(fingerprint) + json.dumps(store.collection_fingerprint(collection))
    # Fingerprint carries only provider/model identifiers, never keys/secrets.
    for forbidden in ("sk-", "Bearer ", "API_AUTH_KEYS", "password"):
        assert forbidden not in blob
    store.reset_vectorstore_clients()
