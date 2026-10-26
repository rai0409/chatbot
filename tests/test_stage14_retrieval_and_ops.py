"""Stage 14 (P0/P1): guarded answers stay guarded, the keyword lane at scale
(candidate pre-selection, tenant pre-filter, lean index, background
refresh), the default tenant in the vector lane, the answer cache, audit
masking, production settings, /chat/stream parity, model loading and the
distance-guard calibration. Synthetic data only.
"""
from __future__ import annotations

import json
import threading
import time
from types import SimpleNamespace

import pytest

import config


# --- 6. guarded answers stay guarded --------------------------------------------------------------


def _answer(guard_reason):
    from schemas import AnswerResult

    return AnswerResult(answer_text="" if guard_reason else "本文", answer_with_footnotes="", intent="other",
                        guard_reason=guard_reason, used_fallback=bool(guard_reason))


def test_profile_fallback_never_turns_an_abstention_into_an_answer(monkeypatch):
    from rag_core import qa

    monkeypatch.setattr(qa, "load_rag_profile", lambda profile_id: {"rules": []})
    monkeypatch.setattr(qa, "detect_question_type", lambda question, profile: "procedure")
    monkeypatch.setattr(qa, "validate_answer_with_profile",
                        lambda *a, **k: {"passed": False, "fallback_if_failed": True})
    monkeypatch.setattr(qa, "extractive_fallback", lambda question, context: pytest.fail("must not extract"))
    guarded = _answer("soft_distance")
    trace = {}
    state = SimpleNamespace(selected_context=[], retrieved=[], rewritten_query="", augmented_query="")
    out = qa._apply_profile_validation("手順は？", guarded, trace, state, rag_profile_id=None, started_at=time.time())
    assert out is guarded and trace["profile_validation"]["skipped"] is True


def test_empty_profile_fallback_keeps_the_answer(monkeypatch):
    from rag_core import qa

    monkeypatch.setattr(qa, "load_rag_profile", lambda profile_id: {"rules": []})
    monkeypatch.setattr(qa, "detect_question_type", lambda question, profile: "procedure")
    monkeypatch.setattr(qa, "validate_answer_with_profile",
                        lambda *a, **k: {"passed": False, "fallback_if_failed": True})
    monkeypatch.setattr(qa, "extractive_fallback", lambda question, context: "")
    answer = _answer(None)
    state = SimpleNamespace(selected_context=[], retrieved=[], rewritten_query="", augmented_query="")
    out = qa._apply_profile_validation("手順は？", answer, {}, state, rag_profile_id=None, started_at=time.time())
    assert out is answer and out.answer_text == "本文"


# --- 8. keyword lane -----------------------------------------------------------------------------


@pytest.fixture
def corpus(tmp_path, monkeypatch):
    from rag_core import retrieval

    path = tmp_path / "keyword.jsonl"
    monkeypatch.setattr(config, "CHUNKS_JSONL_PATH", str(path))
    monkeypatch.setattr(config, "KEYWORD_TOKENIZER", "heuristic")
    monkeypatch.setattr(config, "KEYWORD_INDEX_ENGINE", "inverted")
    monkeypatch.setattr(config, "IGNORE_SEARCHABLE", False)
    monkeypatch.delenv("KEYWORD_INDEX_REFRESH", raising=False)
    retrieval._INDEX_CACHE.update({"path": None, "mtime": None, "index": None})

    def write(rows):
        path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")

    yield path, write
    retrieval._INDEX_CACHE.update({"path": None, "mtime": None, "index": None})


def test_lean_index_rebuilds_rows_and_display_text(corpus):
    from rag_core import retrieval

    path, write = corpus
    write([{"id": "c1", "text": "経費精算の締め日は毎月25日", "display_text": "表示用の本文", "source_doc": "a.pdf",
            "tenant_id": "default", "searchable": 1}])
    index = retrieval._load_keyword_index()
    assert len(index) == 1 and index.display_text(0) == "表示用の本文"
    assert retrieval.chunk_row("c1") == {"id": "c1", "text": "経費精算の締め日は毎月25日", "display_text": "表示用の本文",
                                         "source_doc": "a.pdf", "tenant_id": "default", "searchable": 1}
    assert retrieval.chunk_row("missing") is None
    assert "経費" in "".join(index.vocabulary)


def test_other_tenants_and_filtered_rows_do_not_crowd_out_results(corpus):
    from rag_core import retrieval

    path, write = corpus
    rows = [{"id": f"x{i}", "text": f"経費精算 申請 {i}", "source_doc": f"x{i}.pdf", "tenant_id": "tenant_b",
             "searchable": 1} for i in range(600)]
    rows += [{"id": f"h{i}", "text": f"経費精算 申請 非表示 {i}", "source_doc": f"h{i}.pdf", "tenant_id": "tenant_a",
              "searchable": 0} for i in range(500)]
    rows += [{"id": f"a{i}", "text": f"経費精算 {i}", "source_doc": f"a{i}.pdf", "tenant_id": "tenant_a", "searchable": 1}
             for i in range(3)]
    write(rows)
    hits = retrieval.keyword_retrieve("経費精算 申請", top_k=3, tenant_id="tenant_a")
    assert sorted(h.metadata["id"] for h in hits) == ["a0", "a1", "a2"]


def test_background_refresh_keeps_serving_the_previous_index(corpus, monkeypatch):
    from rag_core import retrieval

    path, write = corpus
    write([{"id": "old", "text": "旧版の本文", "source_doc": "a.pdf", "tenant_id": "default"}])
    first = retrieval._load_keyword_index()
    monkeypatch.setenv("KEYWORD_INDEX_REFRESH", "background")
    time.sleep(0.01)
    write([{"id": "new", "text": "新版の本文", "source_doc": "a.pdf", "tenant_id": "default"}])
    assert retrieval._load_keyword_index() is first  # served at once, rebuilt in a thread
    deadline = time.time() + 10
    while time.time() < deadline and retrieval._load_keyword_index() is first:
        time.sleep(0.05)
    assert retrieval.chunk_row("new") is not None and retrieval.chunk_row("old") is None


# --- 9. default tenant in the vector lane --------------------------------------------------------


def test_default_tenant_is_requeried_when_crowded_out(monkeypatch):
    from rag_core import retrieval, store

    calls = []

    class _Collection:
        def query(self, query_embeddings, n_results, where=None):
            calls.append(where)
            if where and "tenant_id" in json.dumps(where):
                return {"documents": [["自社の本文"]], "metadatas": [[{"id": "d1", "tenant_id": "default"}]],
                        "distances": [[0.2]]}
            n = n_results
            return {"documents": [["他社"] * n], "metadatas": [[{"id": f"o{i}", "tenant_id": "tenant_b"} for i in range(n)]],
                    "distances": [[0.1] * n]}

    monkeypatch.setattr(store, "get_vectorstore", lambda **kwargs: _Collection())
    monkeypatch.setattr(config, "TENANT_FILTER_STRICT", False)
    hits = retrieval.vector_retrieve("締め日", None, top_k=2, query_embedding=[1.0, 0.0])
    assert [h.metadata["id"] for h in hits] == ["d1"] and len(calls) == 2


# --- 10-12. production settings and audit --------------------------------------------------------


def test_production_refuses_chroma_with_several_workers(monkeypatch):
    from webapi import production_guard

    monkeypatch.setenv("WEB_CONCURRENCY", "4")
    monkeypatch.delenv("VECTOR_BACKEND", raising=False)
    assert any("WEB_CONCURRENCY" in p for p in production_guard.production_problems())
    monkeypatch.setenv("VECTOR_BACKEND", "qdrant")
    assert not any("WEB_CONCURRENCY" in p for p in production_guard.production_problems())
    monkeypatch.setenv("ALLOWED_HOSTS", "*")
    assert any("ALLOWED_HOSTS must list" in p for p in production_guard.production_problems())


def test_audit_masks_user_text_by_default_in_production(monkeypatch):
    from rag_core import audit_log

    monkeypatch.delenv("AUDIT_QUESTION_MODE", raising=False)
    monkeypatch.setenv("APP_ENV", "production")
    payload = {"query": "社員番号 12345 の申請", "comment": "連絡先 taro@example.co.jp",
               "conversation_context": {"original_question": "口座 9876 は？"}}
    audit_log._protect_questions(payload)
    assert payload["query"] == "社員番号 ##### の申請"
    assert payload["comment"] == "連絡先 [email]"
    assert payload["conversation_context"]["original_question"] == "口座 #### は？"
    monkeypatch.setenv("APP_ENV", "development")
    assert audit_log._question_mode() == "plain"


# --- 11. answer cache ----------------------------------------------------------------------------


def test_local_answer_cache_expires(monkeypatch):
    from rag_core import answer_cache

    monkeypatch.setattr(config, "ANSWER_CACHE_ENABLED", True)
    monkeypatch.setenv("ANSWER_CACHE_BACKEND", "local")
    monkeypatch.setenv("ANSWER_CACHE_TTL_SECONDS", "60")
    answer_cache.clear()
    now = [1000.0]
    monkeypatch.setattr(answer_cache.time, "time", lambda: now[0])
    answer_cache.put(("k",), {"answer_text": "x"})
    assert answer_cache.get(("k",)) == {"answer_text": "x"}
    now[0] += 61
    assert answer_cache.get(("k",)) is None
    answer_cache.clear()


# --- 14. /chat/stream checks the profile like /chat ----------------------------------------------


def test_stream_rejects_an_invalid_profile(monkeypatch):
    from fastapi.testclient import TestClient

    for name in ("API_AUTH_ENABLED", "APP_ENV", "ENTERPRISE_OIDC_ENABLED", "ENTERPRISE_AUTH_ENABLED"):
        monkeypatch.delenv(name, raising=False)
    from webapi import main

    client = TestClient(main.app)
    resp = client.post("/chat/stream", json={"question": "締め日は？", "rag_profile_id": "../x"})
    assert resp.status_code == 400


# --- 15. model loading ---------------------------------------------------------------------------


def test_concurrent_first_calls_load_a_model_once():
    from rag_core.model_cache import load_once

    loads = []

    @load_once(maxsize=2)
    def load(name):
        loads.append(name)
        time.sleep(0.2)
        return object()

    results = []
    threads = [threading.Thread(target=lambda: results.append(load("m"))) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert loads == ["m"] and len({id(r) for r in results}) == 1
    load.cache_clear()
    load("m")
    assert loads == ["m", "m"]


# --- distance guard calibration ------------------------------------------------------------------


def test_calibration_recommends_thresholds_from_the_set():
    from scripts.calibrate_distance_guard import recommend

    answerable = [0.10, 0.12, 0.15, 0.18, 0.20, 0.22, 0.25, 0.28, 0.30, 0.35]
    unanswerable = [0.40, 0.45, 0.50, 0.55]
    out = recommend(answerable, unanswerable, keep_answerable=0.9)
    assert out["RAG_SOFT_DIST_OTHER"] == 0.30
    assert out["RAG_HARD_MAX_DIST"] == 0.37
    assert out["at_soft"] == {"answerable_answered": 0.9, "unanswerable_abstained": 1.0}
    assert out["best_separation"]["threshold"] == 0.35


def test_calibration_status_reports_a_model_mismatch(monkeypatch):
    from rag_core import embedding_provider, qa

    monkeypatch.setattr(embedding_provider, "active_fingerprint", lambda: {"embed_model": "cl-nagoya/ruri-v3-130m"})
    monkeypatch.setattr(config, "RAG_DIST_CALIBRATED_MODEL", "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2")
    assert qa.distance_guard_calibration()["calibrated"] is False
    monkeypatch.setattr(config, "RAG_DIST_CALIBRATED_MODEL", "cl-nagoya/ruri-v3-130m")
    assert qa.distance_guard_calibration()["calibrated"] is True


def test_vectorised_bm25_scores_equal_the_reference_loop():
    from rag_core.bm25_index import InvertedBM25

    corpus = [["経費", "精算", "申請"], ["申請", "申請", "承認"], ["出張", "旅費"], []]
    bm25 = InvertedBM25(corpus)
    query = ["申請", "精算", "未知語"]
    matched, scores = bm25.score_arrays(query)
    expected = bm25.score(query)
    assert sorted(int(i) for i in matched) == sorted(expected)
    for idx, score in zip(matched, scores):
        assert abs(float(score) - expected[int(idx)]) < 1e-12
