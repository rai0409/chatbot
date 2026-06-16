from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

import config
from rag_core import answer_cache, embedder, qa, store
from rag_core.retrieval import RetrievedChunk
from webapi import ingestion_jobs, main, metrics_registry


class _Recorder:
    def __init__(self, result=None):
        self.calls = []
        self.result = result

    def __call__(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return self.result


def _fake_answer():
    ans = SimpleNamespace(
        intent="other",
        guard_reason=None,
        used_fallback=False,
        citations=[],
        to_dict=lambda: {"answer_text": "staging answer", "citations": [], "retrieved": []},
    )
    trace = {
        "request_id": "req-staging",
        "normalized_query": "質問",
        "intent": "other",
        "final_guard_reason": None,
        "final_used_fallback": False,
        "citations_count": 0,
        "latency_ms": 1,
        "after_rerank": [],
        "answer_mode": "grounded",
    }
    return ans, trace


def _setup(monkeypatch, tmp_path):
    for var in (
        "API_AUTH_ENABLED",
        "API_AUTH_KEYS",
        "API_AUTH_TENANT_MAP",
        "RATE_LIMIT_ENABLED",
        "SEARCH_DEBUG_ENABLED",
    ):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(config, "APPROVED_QA_ENABLED", False)
    monkeypatch.setattr(config, "ANSWER_CACHE_ENABLED", False)
    monkeypatch.setattr(config, "RUNS_DIR", str(tmp_path / "runs"))
    answer_cache.clear()
    metrics_registry.reset()
    ingestion_jobs.reset()
    monkeypatch.setattr(main, "ensure_openai_client", lambda base_url=None: object())
    monkeypatch.setattr(main, "_approved_qa_lookup", lambda *a, **k: None)


def _allow_staging_collection(monkeypatch, tenant_id="tenant_a", collection="tenant_a_stage_v1"):
    monkeypatch.setattr(
        ingestion_jobs,
        "staging_collection_status",
        lambda name, tenant_id: (
            {"allowed": True, "reason": "prompt072_execute_job_found"}
            if name == collection and tenant_id == tenant_id
            else {"allowed": False, "reason": "staging_collection_unknown"}
        ),
    )


def test_chat_default_request_does_not_select_collection(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    recorder = _Recorder(result=_fake_answer())
    monkeypatch.setattr(main, "answer_query_with_trace", recorder)

    payload = main.chat(main.ChatRequest(question="質問です", tenant_id="default"))

    assert payload["query_collection_mode"] == "served_default"
    assert recorder.calls[0][1]["tenant_id"] == "default"
    assert "collection_name" not in recorder.calls[0][1]


def test_chat_staging_collection_is_validated_and_threaded(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    monkeypatch.setattr(
        ingestion_jobs,
        "staging_collection_status",
        lambda name, tenant_id: {"allowed": True, "reason": "prompt072_execute_job_found"},
    )
    monkeypatch.setattr(main, "_approved_qa_lookup", lambda *a, **k: (_ for _ in ()).throw(AssertionError("approved QA bypassed in staging")))
    recorder = _Recorder(result=_fake_answer())
    monkeypatch.setattr(main, "answer_query_with_trace", recorder)

    payload = main.chat(
        main.ChatRequest(
            question="ステージングを検索",
            tenant_id="tenant_a",
            staging_collection="tenant_a_stage_v1",
        )
    )

    assert payload["query_collection_mode"] == "staging"
    assert payload["query_collection"] == "tenant_a_stage_v1"
    assert recorder.calls[0][1]["tenant_id"] == "tenant_a"
    assert recorder.calls[0][1]["collection_name"] == "tenant_a_stage_v1"


def test_staging_collection_rejects_production_default_names(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    monkeypatch.setattr(config, "VECTORSTORE_COLLECTION_NAME", "rag_chunks")

    for name in ("", "default", "rag_chunks"):
        if not name:
            assert main._resolve_staging_collection(name, tenant_id="tenant_a") is None
            continue
        with pytest.raises(HTTPException) as exc:
            main._resolve_staging_collection(name, tenant_id="tenant_a")
        assert exc.value.status_code == 400


def test_unknown_and_wrong_tenant_staging_collections_are_safe_errors(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    monkeypatch.setattr(
        ingestion_jobs,
        "staging_collection_status",
        lambda name, tenant_id: {"allowed": False, "reason": "staging_collection_unknown"},
    )
    with pytest.raises(HTTPException) as exc:
        main._resolve_staging_collection("missing_stage_v1", tenant_id="tenant_a")
    assert exc.value.status_code == 404

    monkeypatch.setattr(
        ingestion_jobs,
        "staging_collection_status",
        lambda name, tenant_id: {"allowed": False, "reason": "staging_collection_forbidden_for_tenant"},
    )
    with pytest.raises(HTTPException) as exc:
        main._resolve_staging_collection("tenant_b_stage_v1", tenant_id="tenant_a")
    assert exc.value.status_code == 403


def test_api_key_tenant_authorization_blocks_before_staging_lookup(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    monkeypatch.setenv("API_AUTH_ENABLED", "true")
    monkeypatch.setenv("API_AUTH_KEYS", "key-a")
    monkeypatch.setenv("API_AUTH_TENANT_MAP", "key-a=tenant_a")
    lookup = _Recorder(result={"allowed": True, "reason": "prompt072_execute_job_found"})
    monkeypatch.setattr(ingestion_jobs, "staging_collection_status", lookup)
    client = TestClient(main.app)

    resp = client.post(
        "/chat/stream",
        json={"question": "q", "tenant_id": "tenant_b", "staging_collection": "tenant_b_stage_v1"},
        headers={"X-Api-Key": "key-a"},
    )

    assert resp.status_code == 403
    assert lookup.calls == []


def test_search_can_specify_staging_collection(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    monkeypatch.setattr(
        ingestion_jobs,
        "staging_collection_status",
        lambda name, tenant_id: {"allowed": True, "reason": "prompt072_execute_job_found"},
    )
    recorder = _Recorder(result=[])
    monkeypatch.setattr(main, "retrieve_chunks", recorder)

    payload = main.search(
        main.SearchRequest(query="検索", tenant_id="tenant_a", staging_collection="tenant_a_stage_v1")
    )

    assert payload["query_collection_mode"] == "staging"
    assert recorder.calls[0][1]["tenant_id"] == "tenant_a"
    assert recorder.calls[0][1]["collection_name"] == "tenant_a_stage_v1"
    assert recorder.calls[0][1]["create_collection_if_missing"] is False


def test_staging_retrieval_uses_existing_selected_vectorstore(monkeypatch):
    calls = []

    class _Collection:
        def query(self, **kwargs):
            return {
                "documents": [["staging text"]],
                "metadatas": [[{"id": "c1", "source_doc": "stage.pdf", "tenant_id": "tenant_a"}]],
                "distances": [[0.12]],
            }

    def fake_get_vectorstore(**kwargs):
        calls.append(kwargs)
        return _Collection()

    monkeypatch.setattr(store, "get_vectorstore", fake_get_vectorstore)
    monkeypatch.setattr(embedder, "embed_queries", lambda *a, **k: [[0.1, 0.2]])

    hits = qa.retrieve_chunks(
        "query",
        client=object(),
        top_k=1,
        tenant_id="tenant_a",
        collection_name="tenant_a_stage_v1",
        create_collection_if_missing=False,
    )

    assert len(hits) == 1
    assert calls[0]["collection_name"] == "tenant_a_stage_v1"
    assert calls[0]["create_if_missing"] is False


def test_chat_ui_shows_staging_selector_and_scope(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    body = TestClient(main.app).get("/chat-ui").text

    assert 'id="stagingCollection"' in body
    assert 'id="tenantBadge"' in body
    assert 'id="collectionBadge"' in body
    assert "staging_collection" in body
    assert "key-a" not in body
