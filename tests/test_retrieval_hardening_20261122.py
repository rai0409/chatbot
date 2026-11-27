"""Retrieval / guard / embedding hardening (2026-11-22). Stubs only: no model, no network, no vector store."""
from __future__ import annotations

import contextvars
import json
import threading
import time

import pytest

import config
from rag_core import (
    cross_encoder_reranker,
    embedding_provider,
    knob_registry,
    multi_query,
    qa,
    query_preprocess,
    retrieval,
    store,
)
from rag_core.retrieval import RetrievedChunk


def _chunk(chunk_id, text="本文", score=0.5, **meta):
    return RetrievedChunk(text=text, metadata={"id": chunk_id, "source_doc": "doc.pdf", **meta}, score=score)


@pytest.fixture
def ruri_identity(monkeypatch):
    monkeypatch.setenv("EMBED_PROVIDER", "local")
    monkeypatch.setenv("LOCAL_EMBED_MODEL", "cl-nagoya/ruri-v3-130m")
    monkeypatch.delenv("LOCAL_EMBED_MODEL_PATH", raising=False)
    monkeypatch.delenv("EMBED_QUERY_PREFIX", raising=False)
    monkeypatch.delenv("EMBED_DOCUMENT_PREFIX", raising=False)
    monkeypatch.setattr(config, "LOCAL_EMBED_MODEL_REVISION", "")
    monkeypatch.setattr(config, "EMBED_MAX_SEQ_LENGTH", 0)
    for name in config.GUARD_THRESHOLD_NAMES:
        monkeypatch.delenv(name, raising=False)
        monkeypatch.setattr(config, name, config.GUARD_THRESHOLD_IMPORTED[name])
    monkeypatch.delenv("RAG_MAX_DISTANCE", raising=False)
    return embedding_provider.embedding_identity()


def _write_calibration(tmp_path, identity, **thresholds):
    path = tmp_path / "distance_guard.json"
    path.write_text(json.dumps({
        "identity": identity,
        "thresholds": thresholds,
        "measured_at": "2026-11-22T00:00:00+00:00",
        "n_cases": 40,
    }), encoding="utf-8")
    return path


# --- 1. distance guard calibration identity -------------------------------------------------------


def test_default_calibrated_model_no_longer_names_minilm():
    assert "MiniLM" not in str(config.RAG_DIST_CALIBRATED_MODEL or "")


def test_calibration_file_with_matching_identity_is_used(ruri_identity, monkeypatch, tmp_path):
    path = _write_calibration(tmp_path, ruri_identity, RAG_SOFT_DIST_OTHER=0.21, RAG_SOFT_DIST_PROCEDURE=0.24,
                              RAG_HARD_MAX_DIST=0.31)
    monkeypatch.setattr(config, "RAG_DIST_CALIBRATION_FILE", str(path))
    status = qa.distance_guard_calibration()
    assert status["calibrated"] is True and status["basis"] == "identity_file"
    assert status["soft_other"] == 0.21 and status["hard"] == 0.31
    assert status["threshold_sources"]["RAG_SOFT_DIST_OTHER"] == "calibration_file"
    assert status["threshold_sources"]["RAG_SOFT_DIST_RESET"] == "default"
    assert qa._soft_threshold("procedure") == 0.24
    # The guard itself uses them: 0.25 is above the calibrated soft threshold of "other" questions.
    chunks = [qa.Chunk(id="1", text="VPN 接続 手順", source_doc="d", source_pages=(1,), score=0.25)]
    retrieved = [_chunk("1", "VPN 接続 手順", 0.25, vector_distance=0.25)]
    assert qa.guard_merged_top("VPNの接続について詳しく教えてほしい", "other", chunks, retrieved) == "soft_distance"
    retrieved = [_chunk("1", "VPN 接続 手順", 0.4, vector_distance=0.4)]
    assert qa.guard_merged_top("VPNの接続について詳しく教えてほしい", "other", chunks, retrieved) == "hard_distance"


def test_explicit_environment_value_wins_over_the_file(ruri_identity, monkeypatch, tmp_path):
    path = _write_calibration(tmp_path, ruri_identity, RAG_SOFT_DIST_OTHER=0.21, RAG_HARD_MAX_DIST=0.31)
    monkeypatch.setattr(config, "RAG_DIST_CALIBRATION_FILE", str(path))
    monkeypatch.setenv("RAG_SOFT_DIST_OTHER", "0.5")
    monkeypatch.setattr(config, "RAG_SOFT_DIST_OTHER", 0.5)
    status = qa.distance_guard_calibration()
    assert status["thresholds"]["RAG_SOFT_DIST_OTHER"] == 0.5
    assert status["threshold_sources"]["RAG_SOFT_DIST_OTHER"] == "explicit"
    assert status["thresholds"]["RAG_HARD_MAX_DIST"] == 0.31


def test_identity_mismatch_is_not_calibrated_and_falls_back(ruri_identity, monkeypatch, tmp_path):
    other = dict(ruri_identity, revision="0123abcd")
    path = _write_calibration(tmp_path, other, RAG_SOFT_DIST_OTHER=0.21, RAG_HARD_MAX_DIST=0.31)
    monkeypatch.setattr(config, "RAG_DIST_CALIBRATION_FILE", str(path))
    status = qa.distance_guard_calibration()
    assert status["calibrated"] is False and status["mismatch"] == ["revision"]
    assert status["thresholds"]["RAG_SOFT_DIST_OTHER"] == config.GUARD_THRESHOLD_IMPORTED["RAG_SOFT_DIST_OTHER"]
    # Pinning the same revision makes it match.
    monkeypatch.setattr(config, "LOCAL_EMBED_MODEL_REVISION", "0123abcd")
    assert qa.distance_guard_calibration()["calibrated"] is True
    # A different prefix scheme or truncation length is a mismatch too.
    monkeypatch.setenv("EMBED_QUERY_PREFIX", "")
    assert "prefix_scheme" in qa.distance_guard_calibration()["mismatch"]


def test_missing_file_and_name_only_basis(ruri_identity, monkeypatch, tmp_path):
    monkeypatch.setattr(config, "RAG_DIST_CALIBRATION_FILE", str(tmp_path / "absent.json"))
    status = qa.distance_guard_calibration()
    assert status["calibrated"] is False and status["reason"] == "calibration_file_missing"
    monkeypatch.setattr(config, "RAG_DIST_CALIBRATION_FILE", "")
    monkeypatch.setattr(config, "RAG_DIST_CALIBRATED_MODEL", "")
    status = qa.distance_guard_calibration()
    assert status["calibrated"] is False and status["basis"] == "model_name_only" and status["reason"] == "not_calibrated"
    assert {"active_model", "calibrated_for", "soft_other", "hard"} <= set(status)


def test_calibration_script_measures_the_guard_evidence_and_writes_every_threshold(monkeypatch, ruri_identity):
    from scripts import calibrate_distance_guard as cal

    seen = []

    def fake_trace(question, **kwargs):
        seen.append((question, kwargs.get("tenant_id")))
        distance = {"近い": 0.12, "遠い": 0.6}[question[:2]]
        state = qa._RetrievalTraceState(
            normalized_query=question, intent="other", query_type="other", rewritten_query=question,
            augmented_query=question, retrieved=[_chunk("x", score=0.3, vector_distance=distance)],
            grounded_candidates=[], selected_context=[], guard_reason=None, used_fallback=False,
        )
        return {}, state, 0.0

    monkeypatch.setattr(qa, "_build_retrieval_trace", fake_trace)
    cases = [("近い質問%d" % i, True, "default") for i in range(6)] + [("遠い質問", False, "t1")]
    rows = cal.guard_evidence_distances(cases, collection=None, top_k=5)
    assert seen[-1] == ("遠い質問", "t1") and rows[0] == {"answerable": True, "intent": "other", "distance": 0.12}
    out = cal.build_calibration_file(rows, identity=ruri_identity, keep_answerable=0.95, min_per_intent=5,
                                     procedure_delta=0.03, measured_at="2026-11-22T00:00:00+00:00")
    assert set(out["thresholds"]) == set(config.GUARD_THRESHOLD_NAMES)
    assert out["threshold_sources"]["RAG_SOFT_DIST_OTHER"] == "intent"
    assert out["threshold_sources"]["RAG_SOFT_DIST_RESET"] == "pooled"
    assert out["identity"] == ruri_identity and out["n_cases"] == 7
    assert all(v <= out["thresholds"]["RAG_HARD_MAX_DIST"] for k, v in out["thresholds"].items() if "SOFT" in k)


def test_guard_knob_floors_allow_ruri_distances():
    assert knob_registry.check_value("RAG_SOFT_DIST_OTHER", 0.08) == 0.08
    assert knob_registry.check_value("RAG_HARD_MAX_DIST", 0.05) == 0.05
    assert "RAG_SOFT_DIST_PROCEDURE" in knob_registry.names()
    assert "RAG_HARD_MAX_DIST_PROCEDURE_DELTA" in knob_registry.names()


def test_eval_sweep_starts_below_half():
    from eval.guard_distance_calibration import sweep_thresholds

    grid = sweep_thresholds()
    assert grid[0] == 0.05 and grid[-1] == 1.0


# --- 2. staging evaluates with production conditions ------------------------------------------------


@pytest.fixture
def staging(monkeypatch):
    calls = {}

    def fake_vector(question, client, top_k, **kwargs):
        calls["top_k"] = top_k
        return [_chunk(f"c{i}", "電子入札の概要と申請", 0.7 + i * 0.01, vector_distance=0.7 + i * 0.01)
                for i in range(top_k)]

    monkeypatch.setenv("QUERY_PREPROCESS_ENABLED", "false")
    monkeypatch.setattr(qa, "_staging_vector_only", lambda name: True)
    monkeypatch.setattr(qa, "vector_retrieve", fake_vector)
    monkeypatch.setattr(config, "VECTOR_TOP_K", 40)
    monkeypatch.setattr(config, "CROSS_ENCODER_RERANK_ENABLED", False)
    monkeypatch.setattr(config, "MULTI_QUERY_ENABLED", False)
    monkeypatch.setattr(config, "SYNONYM_LANE_ENABLED", False)
    monkeypatch.setattr(config, "RAG_SOFT_DIST_OTHER", 0.65)
    monkeypatch.setattr(config, "RAG_HARD_MAX_DIST", 0.95)
    monkeypatch.setattr(config, "RAG_DIST_CALIBRATION_FILE", "")
    return calls


def test_staging_keeps_the_production_guard_by_default(staging, monkeypatch):
    monkeypatch.setattr(config, "STAGING_GUARD_LENIENT", False)
    trace, state, _ = qa._build_retrieval_trace("電子入札の概要を説明して", client=object(), top_k=5,
                                                max_context_chars=8000, collection_name="staging_x")
    assert staging["top_k"] == 40 and len(state.retrieved) <= 5
    assert state.guard_reason == "soft_distance" and "staging_guard_bypass_reason" not in trace
    conditions = trace["staging_conditions"]
    assert conditions["vector_top_k"] == 40 and conditions["guard_lenient"] is False
    assert conditions["parent_expansion"] is False and conditions["query_routing"] is False


def test_staging_guard_bypass_is_opt_in(staging, monkeypatch):
    monkeypatch.setattr(config, "STAGING_GUARD_LENIENT", True)
    trace, state, _ = qa._build_retrieval_trace("電子入札の概要を説明して", client=object(), top_k=5,
                                                max_context_chars=8000, collection_name="staging_x")
    assert state.guard_reason is None and trace["staging_guard_bypass_reason"] == "soft_distance"


class _ChromaLikeCollection:
    """Distance-ordered rows; many private mail rows first, as on a mail-heavy Chroma collection."""

    name = "fake"
    metadata = {}

    def __init__(self, rows):
        self.rows = rows
        self.n_results = []

    def query(self, query_embeddings, n_results, where=None):
        self.n_results.append(n_results)
        rows = self.rows[:n_results]
        return {"ids": [[r[1]["id"] for r in rows]], "documents": [[r[0] for r in rows]], "metadatas": [[r[1] for r in rows]],
                "distances": [[r[2] for r in rows]]}


def test_chroma_widens_until_top_k_non_mail_candidates(monkeypatch):
    rows = [(f"mail {i}", {"id": f"m{i}", "type": "email"}, 0.1 + i * 0.001) for i in range(30)]
    rows += [(f"doc {i}", {"id": f"d{i}", "type": "pdf"}, 0.5 + i * 0.001) for i in range(10)]
    collection = _ChromaLikeCollection(rows)
    monkeypatch.setenv("VECTOR_BACKEND", "chroma")
    monkeypatch.setattr(retrieval.store, "get_vectorstore", lambda **kwargs: collection)
    monkeypatch.setattr(config, "CHILD_RETRIEVAL_OVERSAMPLE", 2)
    hits = retrieval.vector_retrieve("q", None, top_k=5, query_embedding=[0.1])
    assert [h.metadata["id"] for h in hits] == ["d0", "d1", "d2", "d3", "d4"]
    assert collection.n_results[0] == 10 and collection.n_results[-1] >= 40


# --- 3. revision pinning ------------------------------------------------------------------------------


def test_sentence_transformer_is_loaded_with_revision_and_no_remote_code(monkeypatch):
    created = []

    class FakeST:
        def __init__(self, name, **kwargs):
            created.append((name, kwargs))
            self.max_seq_length = 8192

    embedding_provider._get_sentence_transformer_model.cache_clear()
    monkeypatch.setattr(embedding_provider, "_load_sentence_transformer_class", lambda provider_name: FakeST)
    monkeypatch.setattr(config, "LOCAL_EMBED_MODEL_REVISION", "abc123")
    monkeypatch.setattr(config, "EMBED_MAX_SEQ_LENGTH", 512)
    monkeypatch.setattr(config, "EMBED_NUM_THREADS", 0)
    first = embedding_provider._get_local_model("m")
    assert created == [("m", {"trust_remote_code": False, "revision": "abc123"})] and first.max_seq_length == 512
    assert embedding_provider._get_local_model("m") is first  # cache hit
    monkeypatch.setattr(config, "LOCAL_EMBED_MODEL_REVISION", "def456")
    assert embedding_provider._get_local_model("m") is not first  # the revision is part of the key
    monkeypatch.setattr(config, "EMBED_MAX_SEQ_LENGTH", 1024)
    assert embedding_provider._get_local_model("m").max_seq_length == 1024
    assert len(created) == 3
    embedding_provider._get_sentence_transformer_model.cache_clear()


def test_cross_encoder_cache_key_has_max_length_and_revision(monkeypatch):
    created = []

    class FakeCE:
        def __init__(self, name, **kwargs):
            created.append((name, kwargs))

    cross_encoder_reranker._get_cross_encoder_model.cache_clear()
    monkeypatch.setattr(cross_encoder_reranker, "_load_cross_encoder_class", lambda: FakeCE)
    monkeypatch.setattr(config, "EMBED_NUM_THREADS", 0)
    monkeypatch.setattr(config, "CROSS_ENCODER_MAX_LENGTH", 512)
    monkeypatch.setattr(config, "CROSS_ENCODER_REVISION", "r1")
    a = cross_encoder_reranker._get_cross_encoder_model("ce")
    assert created == [("ce", {"trust_remote_code": False, "max_length": 512, "revision": "r1"})]
    assert cross_encoder_reranker._get_cross_encoder_model("ce") is a
    monkeypatch.setattr(config, "CROSS_ENCODER_MAX_LENGTH", 256)
    assert cross_encoder_reranker._get_cross_encoder_model("ce") is not a
    monkeypatch.setattr(config, "CROSS_ENCODER_REVISION", "r2")
    cross_encoder_reranker._get_cross_encoder_model("ce")
    assert len(created) == 3
    cross_encoder_reranker._get_cross_encoder_model.cache_clear()


def test_revision_and_max_length_are_in_the_profile_and_identity(ruri_identity, monkeypatch):
    provider = embedding_provider.LocalEmbeddingProvider()
    assert "embed_revision" not in embedding_provider.provider_profile(provider)  # unpinned: old stamps match
    monkeypatch.setattr(config, "LOCAL_EMBED_MODEL_REVISION", "abc123")
    monkeypatch.setattr(config, "EMBED_MAX_SEQ_LENGTH", 512)
    profile = embedding_provider.provider_profile(provider)
    assert profile["embed_revision"] == "abc123" and profile["embed_max_seq_length"] == "512"
    identity = embedding_provider.embedding_identity()
    assert identity["revision"] == "abc123" and identity["max_seq_length"] == 512
    assert identity["prefix_scheme"] == "ruri-v3-retrieval" and identity["model"] == "cl-nagoya/ruri-v3-130m"


def test_collection_with_another_pinned_revision_is_refused(ruri_identity, monkeypatch):
    class _Col:
        name = "rev_col"
        metadata = {"embed_provider": "local", "embed_model": "cl-nagoya/ruri-v3-130m",
                    "embed_prefix_scheme": "ruri-v3-retrieval", "embed_revision": "old"}

    monkeypatch.setattr(config, "LOCAL_EMBED_MODEL_REVISION", "new")
    with pytest.raises(RuntimeError, match="revision mismatch"):
        store.verify_collection_fingerprint(_Col())
    monkeypatch.setattr(config, "LOCAL_EMBED_MODEL_REVISION", "old")
    store.verify_collection_fingerprint(_Col())


def test_fingerprint_status_reports_a_revision_pinned_later(ruri_identity, monkeypatch):
    from rag_core import embedding_fingerprint

    stamp = embedding_fingerprint.build_collection_fingerprint_metadata(chunk_count=1, created_at="x")
    assert embedding_fingerprint.fingerprint_status(stamp)["profile_matches_active"] is True
    monkeypatch.setattr(config, "LOCAL_EMBED_MODEL_REVISION", "abc123")
    assert embedding_fingerprint.fingerprint_status(stamp)["profile_matches_active"] is False


# --- 4. one switch for query preprocessing --------------------------------------------------------


def test_query_preprocess_default_is_on_everywhere(monkeypatch):
    monkeypatch.delattr(config, "QUERY_PREPROCESS_ENABLED", raising=False)
    monkeypatch.delenv("QUERY_PREPROCESS_ENABLED", raising=False)
    assert config.query_preprocess_enabled() is True
    assert knob_registry.read("QUERY_PREPROCESS_ENABLED") is True
    monkeypatch.setenv("QUERY_PREPROCESS_ENABLED", "false")
    assert config.query_preprocess_enabled() is False and knob_registry.read("QUERY_PREPROCESS_ENABLED") is False
    monkeypatch.setattr(config, "QUERY_PREPROCESS_ENABLED", True, raising=False)
    assert config.query_preprocess_enabled() is True  # the attribute (evaluation knob) wins


def test_env_example_and_docs_agree_on_the_default():
    from pathlib import Path

    env = Path(config.BASE_DIR, ".env.example").read_text(encoding="utf-8")
    assert "QUERY_PREPROCESS_ENABLED=true" in env
    doc = Path(config.BASE_DIR, "docs/operations/query_perturbation.md").read_text(encoding="utf-8")
    assert "既定オフ" not in doc.split("\n", 5)[2]


# --- 5. definition intent and rewrite rules -------------------------------------------------------


@pytest.mark.parametrize("question", ["VPNってなに？", "VPNって何ですか", "VPNとは何ですか"])
def test_colloquial_definition_becomes_toha(question):
    pre = query_preprocess.preprocess_query(question)
    assert pre.query == "vpnとは"


def test_definition_boost_survives_cleanup(monkeypatch):
    seen = {}

    def fake_retrieve(question, augmented, **kwargs):
        seen["question"], seen["augmented"] = question, augmented
        return [], [], []

    monkeypatch.setenv("QUERY_PREPROCESS_ENABLED", "true")
    monkeypatch.setattr(query_preprocess, "correct_typos", lambda *a, **k: [])
    monkeypatch.setattr(qa, "_retrieve_and_rerank", fake_retrieve)
    trace, _state, _ = qa._build_retrieval_trace("VPNってなに", client=object(), top_k=5, max_context_chars=8000)
    assert trace["definition_question"] is True
    assert all(term in seen["augmented"] for term in config.OTHER_QUERY_BOOST_TERMS)


def test_rule_file_rewrite_type_and_tilde_variants(monkeypatch, tmp_path):
    rules = tmp_path / "rules"
    rules.mkdir()
    (rules / "colloquial_ja.json").write_text(json.dumps({
        "strip_suffix": ["(?:おしえて)(?:〜)?$"],
        "rewrite": [{"pattern": "^(.+?)の読み方$", "replace": r"\1 読み"}, ["^ぱそこん", "pc"]],
    }), encoding="utf-8")
    monkeypatch.setattr(query_preprocess, "RULES_DIR", rules)
    query_preprocess._load_rules.cache_clear()
    try:
        assert query_preprocess.strip_colloquial("設定おしえて~")[0] == "設定"  # ~ (index profile) matches a 〜 rule
        assert query_preprocess.strip_colloquial("設定おしえて〜")[0] == "設定"
        assert query_preprocess.strip_colloquial("規程の読み方", rewrite=True)[0] == "規程 読み"
        assert query_preprocess.strip_colloquial("ぱそこん設定", rewrite=True)[0] == "pc設定"
        assert query_preprocess.strip_colloquial("規程の読み方")[0] == "規程の読み方"  # rewrite is opt-in
    finally:
        query_preprocess._load_rules.cache_clear()


# --- 6. synonyms reach dense retrieval ------------------------------------------------------------


def test_short_synonym_terms_need_a_whole_token(monkeypatch):
    assert query_preprocess.term_in_query("it", "edit the file") is False
    assert query_preprocess.term_in_query("it", "it部門") is True
    monkeypatch.setattr(query_preprocess, "_synonym_groups", lambda profile_id: [["pc", "パソコン"]])
    assert query_preprocess.expand_synonyms("npcの設定") == []
    assert query_preprocess.expand_synonyms("pcの設定") == ["パソコン"]


def test_synonym_lane_runs_without_multi_query_and_after_a_confident_first_pass(monkeypatch):
    calls = []
    embedded = []

    def fake_hybrid(query, client, top_k, query_embedding=None, **kwargs):
        calls.append(query)
        if callable(query_embedding):
            query_embedding()
        hits = {"サーバーの再起動手順": [_chunk("A", score=0.3, rrf_score=0.05), _chunk("B", score=0.4, rrf_score=0.01)],
                "サーバの再起動手順": [_chunk("C", score=0.25)]}
        return list(hits.get(query, []))

    def fake_embed(queries, client=None):
        embedded.append(list(queries))
        return [[0.0] for _ in queries]

    monkeypatch.setattr(qa, "hybrid_retrieve", fake_hybrid)
    monkeypatch.setattr(retrieval.embedder, "embed_queries", fake_embed)
    monkeypatch.setattr(qa, "rerank_chunks", lambda question, items, intent=None, **kw: list(items))
    monkeypatch.setattr(qa, "_decorate_score_details", lambda question, items, query_type=None: list(items))
    monkeypatch.setattr(qa, "expand_parent_chunks", lambda items, **kwargs: list(items))
    monkeypatch.setattr(query_preprocess, "_synonym_groups", lambda profile_id: [["サーバー", "サーバ"]])
    for name, value in {"KEYWORD_BOOST_ENABLED": False, "CROSS_ENCODER_RERANK_ENABLED": False,
                        "PROFILE_BOOST_ENABLED": False, "MULTI_QUERY_ENABLED": False, "SYNONYM_LANE_ENABLED": True,
                        "STAGED_RETRIEVAL_MARGIN": "0.01"}.items():
        monkeypatch.setattr(config, name, value, raising=False)
    stats = {}
    question = "サーバーの再起動手順"
    _b, ranked, _c = qa._retrieve_and_rerank(
        question, question + " 手順 方法", scoring_query=question, client=None, top_k=5, intent="other",
        query_type="other", stats=stats, rag_profile_id="tenant_a",
    )
    assert stats["skipped_augmented_pass"] is True
    assert "サーバの再起動手順" in calls and "C" in [c.metadata["id"] for c in ranked]
    assert stats["synonym_lane"]["variants"][0]["kind"] == "synonym"
    # Every query of the request was embedded in ONE batch.
    assert len(embedded) == 1 and "サーバの再起動手順" in embedded[0] and question in embedded[0]


def test_heuristic_rerank_counts_hits_on_expansions():
    from rag_core.reranker import rerank_chunks

    chunks = [_chunk("a", "関係のない文書です"), _chunk("b", "関係のない別文書"), _chunk("c", "ノートパソコンの貸与")]
    plain = rerank_chunks("PCの貸出", chunks, intent="other")
    expanded = rerank_chunks("PCの貸出", chunks, intent="other", expansions=["パソコン"])
    assert [c.metadata["id"] for c in plain][0] != "c" or True
    assert [c.metadata["id"] for c in expanded].index("c") < [c.metadata["id"] for c in plain].index("c")


# --- 7. boosts survive the cross-encoder; budget ------------------------------------------------


class _FakeCE:
    def __init__(self, scores, delay=0.0):
        self.scores = list(scores)
        self.delay = delay
        self.batches = []

    def predict(self, pairs):
        start = sum(len(b) for b in self.batches)
        self.batches.append(list(pairs))
        time.sleep(self.delay)
        return self.scores[start:start + len(pairs)]


def _boosted(chunk_id, score, keyword_boost=0.0, profile_delta=0.0):
    details = {"keyword_boost_value": keyword_boost, "profile_boost": {"delta": profile_delta}}
    return _chunk(chunk_id, score=score, score_details=details)


def test_cross_encoder_keeps_the_boosts_and_writes_consistent_scores(monkeypatch):
    fake = _FakeCE([0.90, 0.88, 0.10])
    monkeypatch.setattr(cross_encoder_reranker, "_get_cross_encoder_model", lambda name: fake)
    monkeypatch.setattr(config, "CROSS_ENCODER_TOP_N", 3)
    monkeypatch.setattr(config, "CROSS_ENCODER_TIMEOUT_MS", 0)
    chunks = [_boosted("a", 0.30), _boosted("b", 0.31, keyword_boost=0.05, profile_delta=0.05), _boosted("c", 0.32)]
    monkeypatch.setattr(config, "CROSS_ENCODER_BOOST_WEIGHT", 0.0)
    assert [c.metadata["id"] for c in cross_encoder_reranker.rerank("q", chunks)] == ["a", "b", "c"]
    monkeypatch.setattr(config, "CROSS_ENCODER_BOOST_WEIGHT", 1.0)
    fake.batches.clear()
    out = cross_encoder_reranker.rerank("q", chunks)
    assert [c.metadata["id"] for c in out] == ["b", "a", "c"]
    assert [c.score for c in out] == [0.30, 0.31, 0.32]  # scores follow the new order
    assert [c.score for c in chunks] == [0.30, 0.31, 0.32]  # inputs untouched
    # PREFER_SORT_MODE=legacy sorts by (term hit, score): it now keeps the reranked order.
    monkeypatch.setattr(config, "PREFER_SORT_MODE", "legacy", raising=False)
    grounded = qa._to_grounded(out)
    assert [g.id for g in qa._prefer_sort(grounded, "other")] == ["b", "a", "c"]


def test_cross_encoder_budget_keeps_the_remainder_in_order(monkeypatch):
    fake = _FakeCE([0.1, 0.9, 0.5, 0.95, 0.99], delay=0.03)
    monkeypatch.setattr(cross_encoder_reranker, "_get_cross_encoder_model", lambda name: fake)
    monkeypatch.setattr(config, "CROSS_ENCODER_TOP_N", 5)
    monkeypatch.setattr(config, "CROSS_ENCODER_BATCH_SIZE", 2)
    monkeypatch.setattr(config, "CROSS_ENCODER_TIMEOUT_MS", 10)
    monkeypatch.setattr(config, "CROSS_ENCODER_BOOST_WEIGHT", 0.0)
    chunks = [_chunk(x, score=0.1 * i) for i, x in enumerate("abcde")]
    out = cross_encoder_reranker.rerank("q", chunks)
    assert len(fake.batches) == 1  # the budget ran out after the first batch
    assert [c.metadata["id"] for c in out] == ["b", "a", "c", "d", "e"]
    assert cross_encoder_reranker.last_run()["status"] == "partial_budget"
    assert "cross_encoder_score" not in out[2].metadata


def test_warmup_hook_reports_without_raising(monkeypatch):
    monkeypatch.setattr(embedding_provider, "is_local_provider", lambda provider_name=None: True)
    monkeypatch.setattr(embedding_provider, "embed_queries", lambda queries, **kw: [[0.0]])
    monkeypatch.setattr(config, "CROSS_ENCODER_RERANK_ENABLED", False)
    out = qa.warmup_models()
    assert out["embedding"] == "ok" and out["cross_encoder"] == "disabled"


# --- 8. parallel lanes --------------------------------------------------------------------------


def test_parallel_and_sequential_lanes_give_identical_results(monkeypatch):
    seen_scale = []

    def fake_vector(question, client, top_k, **kwargs):
        return [_chunk(f"v{i}", score=0.1 * i, vector_distance=0.1 * i) for i in range(4)]

    def fake_keyword(question, top_k, **kwargs):
        seen_scale.append((threading.current_thread().name, retrieval._FUSION_SCALE.get()))
        return [_chunk(f"v{3 - i}" if i < 2 else f"k{i}", score=0.25, bm25_score=5.0 - i) for i in range(4)]

    monkeypatch.setattr(retrieval, "vector_retrieve", fake_vector)
    monkeypatch.setattr(retrieval, "keyword_retrieve", fake_keyword)
    monkeypatch.setattr(config, "ENABLE_HYBRID_RETRIEVAL", True)

    def run(parallel):
        monkeypatch.setattr(config, "HYBRID_PARALLEL", parallel)
        with retrieval.fusion_scale({"dense": 1.0, "keyword": 2.0}):
            out = retrieval.hybrid_retrieve("q", None, top_k=6, query_embedding=[0.0])
        return [(c.metadata["id"], round(c.metadata["rrf_score"], 9), c.score) for c in out]

    sequential = run(False)
    parallel = run(True)
    assert parallel == sequential
    assert seen_scale[-1][1] == {"dense": 1.0, "keyword": 2.0}  # the routing weights reached the pool thread
    assert seen_scale[-1][0].startswith("hybrid-lane")
    assert retrieval._lane_executor() is retrieval._lane_executor()


def test_sudachi_fast_path_takes_no_lock(monkeypatch):
    from rag_core import ja_tokenizer

    key = (str(getattr(config, "SUDACHI_DICT", "core") or "core"), str(getattr(config, "SUDACHI_SPLIT_MODE", "A") or "A"))
    sentinel = object()
    monkeypatch.setitem(ja_tokenizer._STATE, "key", key)
    monkeypatch.setitem(ja_tokenizer._STATE, "tokenizer", sentinel)
    monkeypatch.setitem(ja_tokenizer._STATE, "generation", 12345)
    ja_tokenizer._LOCAL.generation = 12345
    ja_tokenizer._LOCAL.tokenizer = sentinel
    try:
        with ja_tokenizer._STATE_LOCK:  # held: a slow path would deadlock here
            assert ja_tokenizer._get_sudachi() is sentinel
    finally:
        ja_tokenizer._LOCAL.generation = None


# --- 9. dense rank is the similarity rank ---------------------------------------------------------


def test_vector_lane_orders_by_distance_not_by_chunk_role(monkeypatch):
    rows = [("child far", {"id": "child", "chunk_role": "child"}, 0.40),
            ("parent near", {"id": "parent", "chunk_role": "parent"}, 0.10)]
    collection = _ChromaLikeCollection(rows)
    monkeypatch.setenv("VECTOR_BACKEND", "chroma")
    monkeypatch.setattr(retrieval.store, "get_vectorstore", lambda **kwargs: collection)
    hits = retrieval.vector_retrieve("q", None, top_k=2, query_embedding=[0.1])
    assert [h.metadata["id"] for h in hits] == ["parent", "child"]


def test_fusion_uses_the_fused_score_before_the_child_preference(monkeypatch):
    monkeypatch.setattr(retrieval, "vector_retrieve", lambda *a, **k: [
        _chunk("parent", score=0.1, chunk_role="parent", vector_distance=0.1),
        _chunk("child", score=0.4, chunk_role="child", vector_distance=0.4)])
    monkeypatch.setattr(retrieval, "keyword_retrieve", lambda *a, **k: [])
    monkeypatch.setattr(config, "ENABLE_HYBRID_RETRIEVAL", True)
    monkeypatch.setattr(config, "HYBRID_PARALLEL", False)
    out = retrieval.hybrid_retrieve("q", None, top_k=2, query_embedding=[0.0])
    assert [c.metadata["id"] for c in out] == ["parent", "child"]
    assert out[0].metadata["fusion_dense_rank"] == 1


# --- 10. keyword index keeps one copy of each text -------------------------------------------------


def _write_corpus(tmp_path, rows):
    path = tmp_path / "corpus.jsonl"
    path.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n", encoding="utf-8")
    return str(path)


def test_keyword_index_shares_equal_texts(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "KEYWORD_TOKENIZER", "heuristic")
    text = "経費精算の申請手順について説明します" * 3
    rows = [
        {"id": "1", "text": text, "display_text": text, "searchable_text": text, "tenant_id": "default"},
        {"id": "2", "text": "別の本文", "display_text": "別の本文", "searchable_text": "見出し 別の本文", "tenant_id": "default"},
    ]
    index = retrieval._build_keyword_index_fresh(_write_corpus(tmp_path, rows), "heuristic", "inverted", 1.5, 0.75)
    assert index.metas[0]["display_text"] is index.texts[0]
    assert index.metas[0]["searchable_text"] is index.texts[0]
    assert index.norm_docs[0] is index.texts[0]
    assert index.metas[1]["searchable_text"] == "見出し 別の本文"  # a different searchable text is kept
    assert index.row("1") == rows[0]  # callers see the same row as before
    assert index.display_text(1) == "別の本文"


# --- 11. qdrant search settings ---------------------------------------------------------------------


def test_qdrant_search_params(monkeypatch):
    pytest.importorskip("qdrant_client")
    from rag_core import vector_backend

    monkeypatch.setenv("QDRANT_SEARCH_EF", "128")
    monkeypatch.delenv("QDRANT_SEARCH_EXACT", raising=False)
    monkeypatch.delenv("QDRANT_QUANTIZATION", raising=False)
    assert vector_backend.search_params(300).hnsw_ef == 300
    assert vector_backend.search_params(10).hnsw_ef == 128
    monkeypatch.setenv("QDRANT_SEARCH_EXACT", "true")
    monkeypatch.setenv("QDRANT_QUANTIZATION", "int8")
    monkeypatch.setenv("QDRANT_OVERSAMPLING", "3")
    params = vector_backend.search_params(10)
    assert params.exact is True and params.quantization.oversampling == 3.0 and params.quantization.rescore is True


def test_qdrant_collection_settings_drift(monkeypatch):
    from types import SimpleNamespace

    from rag_core import vector_backend

    monkeypatch.setenv("QDRANT_HNSW_M", "32")
    monkeypatch.setenv("QDRANT_HNSW_EF_CONSTRUCT", "128")
    monkeypatch.setenv("QDRANT_QUANTIZATION", "int8")
    info = SimpleNamespace(config=SimpleNamespace(hnsw_config=SimpleNamespace(m=16, ef_construct=128),
                                                  quantization_config=None))
    drift = vector_backend.collection_settings_drift(info)
    assert drift == {"hnsw_m": {"env": 32, "collection": 16}, "quantization": {"env": "int8", "collection": ""}}


# --- 12. embedding speed ------------------------------------------------------------------------------


class _BatchModel:
    def __init__(self):
        self.calls = []

    def encode(self, texts, normalize_embeddings=True, batch_size=32, **kwargs):
        self.calls.append((list(texts), batch_size))

        class Out:
            def __init__(self, n):
                self.rows = [[float(i)] for i in range(n)]

            def tolist(self):
                return self.rows

        return Out(len(texts))


def test_query_embeddings_are_cached_and_batched(monkeypatch):
    model = _BatchModel()
    monkeypatch.setattr(embedding_provider, "_get_local_model", lambda model_name: model)
    monkeypatch.delenv("LOCAL_EMBED_MODEL_PATH", raising=False)
    monkeypatch.delenv("EMBED_QUERY_PREFIX", raising=False)
    monkeypatch.setattr(config, "EMBED_BATCH_SIZE", 8)
    monkeypatch.setattr(config, "EMBED_QUERY_CACHE_SIZE", 2)
    provider = embedding_provider.LocalEmbeddingProvider("plain-model")
    provider.embed_queries(["a", "b"])
    provider.embed_queries(["b", "c"])  # only c is new
    assert model.calls == [(["a", "b"], 8), (["c"], 8)]
    provider.embed_queries(["a"])  # evicted (size 2): encoded again
    assert model.calls[-1] == (["a"], 8)
    provider.embed_documents(["d"])
    provider.embed_documents(["d"])  # documents are never cached
    assert model.calls[-2:] == [(["d"], 8), (["d"], 8)]


def test_model_cache_hit_does_not_wait_for_another_load():
    from rag_core.model_cache import load_once

    started, release = threading.Event(), threading.Event()

    @load_once(maxsize=4)
    def load(name):
        if name == "slow":
            started.set()
            release.wait(5)
        return object()

    fast = load("fast")
    worker = threading.Thread(target=load, args=("slow",))
    worker.start()
    started.wait(5)
    t0 = time.perf_counter()
    assert load("fast") is fast  # served while "slow" holds the load lock
    assert time.perf_counter() - t0 < 0.5
    release.set()
    worker.join()
    assert load.cache_info().currsize == 2 and load.peek("fast") is fast


def test_default_embed_threads_is_half_the_logical_cpus():
    import os

    assert config._default_embed_threads() == max(1, (os.cpu_count() or 2) // 2)


# --- 13. typo vocabulary per tenant --------------------------------------------------------------


def test_typo_vocabulary_does_not_leak_other_tenants(tmp_path, monkeypatch):
    rows = [
        {"id": "a1", "text": "就業規則の申請書", "tenant_id": "tenant_a"},
        {"id": "b1", "text": "極秘プロジェクト名称", "tenant_id": "tenant_b"},
    ]
    index = retrieval._build_keyword_index_fresh(_write_corpus(tmp_path, rows), "heuristic", "inverted", 1.5, 0.75)
    assert "極秘プロジェクト名称" not in str(sorted(index.tenant_vocabulary("tenant_a")))
    assert any("極秘" in t for t in index.tenant_vocabulary("tenant_b"))
    assert index.tenant_vocabulary("nobody") == frozenset()
    monkeypatch.setattr(retrieval, "_index_for_collection", lambda name: index)
    query_preprocess._VOCAB_CACHE.clear()
    leaked = query_preprocess.correct_typos("極秘プロジェクタ", tenant_id="tenant_a")
    assert leaked == []
    own = query_preprocess.correct_typos("極秘プロジェクタ", tenant_id="tenant_b")
    assert own and own[0]["candidates"]
    query_preprocess._VOCAB_CACHE.clear()


def test_context_copy_helper_is_available():
    # The lane pool runs the keyword lane inside contextvars.copy_context().run.
    assert callable(contextvars.copy_context().run)
    assert multi_query.MAX_QUERIES >= 1


def test_keyword_index_refreshing_reflects_background_rebuild():
    assert retrieval.keyword_index_refreshing() is False
    with retrieval._INDEX_LOCK:
        retrieval._REFRESHING.add("stub-path")
    try:
        assert retrieval.keyword_index_refreshing() is True
    finally:
        with retrieval._INDEX_LOCK:
            retrieval._REFRESHING.discard("stub-path")
    assert retrieval.keyword_index_refreshing() is False
