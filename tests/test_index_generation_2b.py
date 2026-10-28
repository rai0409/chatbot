import json
import uuid
from types import SimpleNamespace

import pytest

qdrant_client = pytest.importorskip("qdrant_client")

import config
from rag_core import answer_cache, index_generation, index_manifest, retrieval, store
from rag_core.vector_backend import QdrantCollection


def _row(i, tenant="ta", doc="d.pdf", text=None):
    return {"id": i, "text": text or f"text {i}", "source_doc": doc, "tenant_id": tenant}


@pytest.fixture
def env(monkeypatch, tmp_path):
    from scripts import ingest_canonical_jsonl as ingest

    monkeypatch.setattr(config, "INDEX_DIR", str(tmp_path / "index"))
    monkeypatch.setattr(config, "VECTORSTORE_DIR", str(tmp_path / "vs"))
    monkeypatch.setattr(config, "CHUNKS_JSONL_PATH", str(tmp_path / "legacy_served.jsonl"))
    monkeypatch.delenv("CHROMA_COLLECTION", raising=False)
    monkeypatch.delenv("ACTIVE_GENERATION_FILE", raising=False)
    monkeypatch.setenv("EMBED_PROVIDER", "local")
    client = qdrant_client.QdrantClient(":memory:")
    cols = {}

    def get_vs(collection_name=None, *, verify_embedding_fingerprint=True, create_if_missing=True):
        name = config.resolve_chroma_collection_name(collection_name)
        if name not in cols:
            if not create_if_missing:
                raise ValueError(f"collection {name} does not exist")
            cols[name] = QdrantCollection(name, client=client)
        return cols[name]

    monkeypatch.setattr(store, "get_vectorstore", get_vs)
    monkeypatch.setattr(ingest.embedder, "is_local_provider", lambda: True)

    def name(prefix):
        return f"{prefix}_{uuid.uuid4().hex[:8]}"

    def build(collection, rows, *, dim=2, register=True, **manifest_kwargs):
        monkeypatch.setattr(
            ingest.embedder, "embed_documents", lambda texts, client=None: [[1.0] * dim for _ in texts]
        )
        generation = uuid.uuid4().hex
        ingest.ingest_canonical_rows(rows, collection_name=collection, generation_id=generation)
        ingest.write_keyword_rows(retrieval.collection_corpus_path(collection), rows, generation_id=generation)
        if register:
            index_manifest.write_manifest(index_manifest.build_manifest(collection, **manifest_kwargs))
        return cols[collection]

    return SimpleNamespace(build=build, name=name, cols=cols, tmp=tmp_path, ingest=ingest)


def _failed(excinfo):
    return {c["name"] for c in excinfo.value.report["checks"] if not c["ok"]}


def test_without_a_pointer_nothing_changes(env):
    assert index_generation.active_pointer() is None
    assert config.resolve_chroma_collection_name() == config.VECTORSTORE_COLLECTION_NAME
    assert retrieval.keyword_index_path_for_collection(None) == config.CHUNKS_JSONL_PATH


def test_activation_switches_name_corpus_and_production_names(env):
    from webapi import ingestion_jobs

    g1 = env.name("g1")
    env.build(g1, [_row("a1"), _row("a2")])
    result = index_generation.activate(g1)
    assert result["previous"] is None
    assert config.resolve_chroma_collection_name() == g1
    corpus = retrieval.collection_corpus_path(g1)
    assert retrieval.keyword_index_path_for_collection(None) == corpus
    assert retrieval.keyword_index_path_for_collection(g1) == corpus
    assert index_generation.served_keyword_path() == corpus
    assert ingestion_jobs.is_production_collection(g1)
    assert retrieval.keyword_index_status()["keyword_index_path"] == corpus
    assert retrieval.keyword_index_status()["keyword_index_records"] == 2
    assert answer_cache._corpus_state()[0][0] == corpus


def test_switch_and_rollback_move_between_generations(env):
    g1, g2 = env.name("g1"), env.name("g2")
    env.build(g1, [_row("a1"), _row("a2")])
    env.build(g2, [_row("b1"), _row("b2"), _row("b3")])
    index_generation.activate(g1)
    assert retrieval.keyword_index_status()["keyword_index_records"] == 2
    result = index_generation.activate(g2)
    assert result["previous"]["collection"] == g1
    assert config.resolve_chroma_collection_name() == g2
    assert retrieval.keyword_index_status()["keyword_index_records"] == 3
    rolled = index_generation.rollback()
    assert rolled["rolled_back_from"]["collection"] == g2
    assert config.resolve_chroma_collection_name() == g1
    assert retrieval.keyword_index_status()["keyword_index_records"] == 2
    with pytest.raises(index_generation.GenerationError, match="no previous generation"):
        index_generation.rollback()
    with pytest.raises(index_generation.GenerationError, match="already active"):
        index_generation.activate(g1)


def test_clear_returns_to_the_environment_settings(env):
    g1 = env.name("g1")
    env.build(g1, [_row("a1")])
    index_generation.activate(g1)
    index_generation.clear()
    assert index_generation.active_pointer() is None
    assert config.resolve_chroma_collection_name() == config.VECTORSTORE_COLLECTION_NAME
    assert retrieval.keyword_index_path_for_collection(None) == config.CHUNKS_JSONL_PATH


def test_activation_needs_a_manifest(env):
    g1 = env.name("g1")
    env.build(g1, [_row("a1")], register=False)
    with pytest.raises(index_generation.GenerationError) as excinfo:
        index_generation.activate(g1)
    assert _failed(excinfo) == {"manifest"}
    assert index_generation.active_pointer() is None


def test_activation_refuses_vector_rows_missing_from_the_corpus(env):
    g1 = env.name("g1")
    col = env.build(g1, [_row("a1")])
    col.upsert(ids=["extra"], documents=["d"], embeddings=[[1.0, 1.0]], metadatas=[{"tenant_id": "ta"}])
    with pytest.raises(index_generation.GenerationError) as excinfo:
        index_generation.activate(g1)
    assert "index_consistency" in _failed(excinfo)
    assert index_generation.active_pointer() is None


def test_activation_refuses_keyword_rows_missing_from_the_vectors(env):
    g1 = env.name("g1")
    env.build(g1, [_row("a1")])
    env.ingest.write_keyword_rows(retrieval.collection_corpus_path(g1), [_row("ghost")], generation_id="x")
    with pytest.raises(index_generation.GenerationError) as excinfo:
        index_generation.activate(g1)
    assert "index_consistency" in _failed(excinfo)


def test_activation_refuses_a_profile_that_differs_from_the_running_provider(env):
    g1 = env.name("g1")
    col = env.build(g1, [_row("a1")])
    col.modify(metadata={**col.metadata, "embed_distance": "dot"})
    with pytest.raises(index_generation.GenerationError) as excinfo:
        index_generation.activate(g1)
    assert {"profile_matches_manifest", "profile_matches_runtime"} <= _failed(excinfo)


def test_activation_refuses_a_wrong_embedding_dimension(env):
    g1 = env.name("g1")
    col = env.build(g1, [_row("a1")])
    col.modify(metadata={**col.metadata, "embedding_dim": 3})
    with pytest.raises(index_generation.GenerationError) as excinfo:
        index_generation.activate(g1)
    assert "embedding_dim" in _failed(excinfo)


def test_activation_refuses_rows_without_a_tenant(env):
    g1 = env.name("g1")
    col = env.build(g1, [_row("a1"), _row("a2")])
    col.update(ids=["a1"], metadatas=[{"tenant_id": ""}])
    with pytest.raises(index_generation.GenerationError) as excinfo:
        index_generation.activate(g1)
    assert "tenant_id_present" in _failed(excinfo)


def test_counts_must_match_the_manifest_until_it_is_marked_stale(env):
    g1 = env.name("g1")
    env.build(g1, [_row("a1"), _row("a2")])
    # The generation changed after it was registered (another document: the same document would be replaced).
    env.build(g1, [_row("a3", doc="other.pdf")], register=False)
    with pytest.raises(index_generation.GenerationError) as excinfo:
        index_generation.activate(g1)
    assert _failed(excinfo) == {"counts_match_manifest"}
    assert index_manifest.mark_stale(g1) is True
    index_generation.activate(g1)
    manifest = index_manifest.read_manifest(g1)
    assert manifest["point_count"] == 3 and manifest["keyword_rows"] == 3
    assert manifest["verification"]["status"] == "verified"


def test_mark_stale_without_a_manifest_does_nothing(env):
    assert index_manifest.mark_stale(env.name("none")) is False


def test_environment_override_wins_over_the_pointer(env, monkeypatch):
    g1 = env.name("g1")
    env.build(g1, [_row("a1")])
    index_generation.activate(g1)
    monkeypatch.setenv("CHROMA_COLLECTION", "from_env")
    assert config.resolve_chroma_collection_name() == "from_env"
    assert index_generation.served_keyword_path() == config.CHUNKS_JSONL_PATH
    with pytest.raises(index_generation.GenerationError, match="CHROMA_COLLECTION"):
        index_generation.activate(env.name("g2"))


def test_a_corrupt_pointer_fails_closed(env):
    pointer = index_generation.pointer_path()
    pointer.parent.mkdir(parents=True, exist_ok=True)
    pointer.write_text("{ not json", encoding="utf-8")
    with pytest.raises(index_generation.PointerError):
        config.resolve_chroma_collection_name()


def test_a_pointer_written_by_another_process_is_picked_up(env):
    g1, g2 = env.name("g1"), env.name("g2")
    env.build(g1, [_row("a1")])
    index_generation.activate(g1)
    assert index_generation.active_collection() == g1
    pointer = index_generation.pointer_path()
    entry = {"collection": g2, "keyword_index_path": retrieval.collection_corpus_path(g2), "generation_id": "x"}
    pointer.write_text(
        json.dumps({"schema_version": 1, "active": entry, "history": [], "note": "written elsewhere"}), encoding="utf-8"
    )
    assert index_generation.active_collection() == g2


def test_vectors_are_not_copied_across_embedding_profiles(env):
    g1, other = env.name("g1"), env.name("other")
    env.build(g1, [_row("a1")])
    col = env.build(other, [_row("a1")], register=False)
    col.modify(metadata={**col.metadata, "embed_model": "another-model"})
    with pytest.raises(ValueError, match="Re-embed"):
        index_manifest.build_manifest(other, derived_from=g1)
    manifest = index_manifest.build_manifest(other, derived_from=g1, reembedded=True)
    assert manifest["derived_from"] == g1 and manifest["reembedded"] is True
    with pytest.raises(ValueError, match="no manifest"):
        index_manifest.build_manifest(other, derived_from=env.name("unregistered"))


def test_compare_reports_profile_and_id_differences(env):
    from scripts.compare_index_generations import compare_generations

    g1, g2 = env.name("g1"), env.name("g2")
    env.build(g1, [_row("a1"), _row("a2")])
    env.build(g2, [_row("a2"), _row("a3")], dim=3)
    report = compare_generations(g1, g2)
    assert report["requires_reembedding"] is True
    assert report["profile_differences"] == {"embedding_dim": {"a": 2, "b": 3}}
    assert report["same_ids"] is False
    assert report["only_in_a"] == ["a1"] and report["only_in_b"] == ["a3"]
    assert report["counts"] == {"a": 2, "b": 2, "common": 1}
    with pytest.raises(LookupError):
        compare_generations(g1, env.name("unregistered"))


def test_switch_cli_registers_verifies_and_activates(env, capsys):
    from scripts import switch_generation

    g1 = env.name("g1")
    env.build(g1, [_row("a1")], register=False)
    assert switch_generation.main(["register", "--collection", g1]) == 0
    assert switch_generation.main(["register", "--collection", g1]) == 1  # no silent overwrite
    assert switch_generation.main(["verify", "--collection", g1]) == 0
    assert switch_generation.main(["activate", "--collection", g1]) == 0
    assert switch_generation.main(["status"]) == 0
    assert switch_generation.main(["activate", "--collection", env.name("missing")]) == 1
    assert switch_generation.main(["rollback"]) == 1
    assert switch_generation.main(["clear"]) == 0
    capsys.readouterr()
    assert index_generation.active_pointer() is None
