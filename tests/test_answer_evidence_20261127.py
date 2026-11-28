"""2026-11-27: citation version evidence, the server-side evidence record, /chat/source staleness, records keep pages,
conversation turns and feedback linked to server evidence, and the stream keeps the caller's audit actor.
Stubs and synthetic data only."""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import config
from rag_core import answer_cache, answer_evidence, audit_log, qa, records
from rag_core.retrieval import RetrievedChunk
from rag_grounded import Chunk, build_citation_payloads, merge_by_page, text_sha256
from schemas import AnswerResult, CitationOut
from webapi import main
from webapi.api_auth import ApiAuthContext
from webapi.rate_limit import require_api_auth_rate_limited
from webapi.routers import chat as chat_router


# --- citations carry version evidence ---------------------------------------------------------------------------


def test_to_grounded_keeps_version_metadata_and_citations_emit_it():
    chunk = RetrievedChunk(text="申請は3営業日前までです。", score=0.1, metadata={
        "id": "c1", "source_doc": "規程.pdf", "source_pages": [4], "index_generation": "gen-2",
        "doc_checksum": "sum-1", "doc_version": "v3", "tenant_id": "default"})
    grounded = qa._to_grounded([chunk])
    assert grounded[0].meta == {"index_generation": "gen-2", "doc_checksum": "sum-1", "doc_version": "v3"}
    payload = build_citation_payloads("- 3営業日前までです。[S1]", grounded)[0]
    assert payload["chunk_sha256"] == text_sha256("申請は3営業日前までです。")
    assert payload["doc_checksum"] == "sum-1" and payload["index_generation"] == "gen-2"
    assert payload["collection"] is None
    CitationOut(**payload)  # the schema accepts every emitted field


def test_merge_by_page_keeps_only_agreeing_metadata():
    a = Chunk("a", "一", "d.pdf", (1,), meta={"index_generation": "g1", "doc_checksum": "x"})
    b = Chunk("b", "二", "d.pdf", (1,), meta={"index_generation": "g1", "doc_checksum": "y"})
    merged = merge_by_page([a, b])
    assert len(merged) == 1 and merged[0].meta == {"index_generation": "g1"}
    assert merged[0].member_ids == ("a", "b")
    assert Chunk("a", "一", "d.pdf", (1,)) == Chunk("a", "一", "d.pdf", (1,), meta={"k": "v"})  # meta is not compared


def test_citation_fields_default_to_none_for_old_payloads():
    out = CitationOut(number=1, source_doc="d.pdf")
    assert out.chunk_sha256 is None and out.doc_checksum is None and out.index_generation is None


# --- records keep pages -----------------------------------------------------------------------------------------


def test_records_keep_source_pages_and_member_ids(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "RECORDS_DIR", str(tmp_path / "records"))
    monkeypatch.setattr(config, "RECORD_LEVEL_MAX", "standard")
    monkeypatch.setattr(records, "effective_level", lambda tenant: "standard")
    rec = records.record_interaction(tenant_id="default", question="質問", answer_text="回答", citations=[
        {"number": 1, "source_doc": "規程.pdf", "source_pages": [3, 4], "chunk_id": "c1", "member_chunk_ids": ["c1", "c2"],
         "supporting_sentences": ["本文"], "chunk_sha256": "ab" * 32},
        {"source_doc": "旧.pdf", "page": 7, "chunk_id": "c9"},
    ])
    assert rec["citations"][0] == {"number": 1, "source_doc": "規程.pdf", "source_pages": [3, 4], "chunk_id": "c1",
                                   "member_chunk_ids": ["c1", "c2"], "chunk_sha256": "ab" * 32}
    assert rec["citations"][1] == {"source_doc": "旧.pdf", "page": 7, "chunk_id": "c9"}


# --- the evidence record through /chat --------------------------------------------------------------------------


def _pipeline(calls, text="申請は3営業日前までです。"):
    def fake(question, **kwargs):
        calls.append(question)
        ans = AnswerResult(
            answer_text="3営業日前までです。", answer_with_footnotes="3営業日前までです。", intent="other",
            guard_reason=None, used_fallback=False,
            citations=[CitationOut(number=1, source_doc="規程.pdf", source_pages=[4], chunk_id="c1",
                                   member_chunk_ids=["c1"], chunk_sha256=text_sha256(text), index_generation="gen-1")],
        )
        trace = {"request_id": f"srv-{len(calls)}", "normalized_query": question, "intent": "other",
                 "final_guard_reason": None, "final_used_fallback": False, "citations_count": 1, "latency_ms": 1,
                 "after_rerank": [], "answer_mode": "grounded", "selected_context_chunk_ids": ["c1"],
                 "rag_profile_id": "default", "rag_profile_version": "7",
                 "cited": [{"chunk_id": "c1", "source_row_sha256": text_sha256(text), "index_generation": "gen-1"}],
                 "active_generation_id": "gen-1",
                 "profile_validation": {"passed": True, "skipped": False}}
        return ans, trace

    return fake


@pytest.fixture
def env(monkeypatch, tmp_path):
    for name in ("API_AUTH_ENABLED", "API_AUTH_KEYS", "API_AUTH_TENANT_MAP", "RATE_LIMIT_ENABLED",
                 "CONVERSATION_CONTEXT_ENABLED", "ANSWER_CACHE_STATE_MEMO_MS", "ANSWER_CACHE_BACKEND",
                 "AUDIT_QUESTION_MODE", "RETRIEVAL_ACL_SCOPED", "EVIDENCE_LOOKUP_CACHE_SIZE"):
        monkeypatch.delenv(name, raising=False)
    chunks = tmp_path / "chunks.jsonl"
    chunks.write_text(json.dumps({"id": "c1", "text": "申請は3営業日前までです。", "source_doc": "規程.pdf",
                                  "source_pages": [4], "index_generation": "gen-1"}, ensure_ascii=False) + "\n",
                      encoding="utf-8")
    monkeypatch.setattr(config, "CHUNKS_JSONL_PATH", str(chunks))
    monkeypatch.setattr(config, "APPROVED_QA_ENABLED", False)
    monkeypatch.setattr(config, "ANSWER_CACHE_ENABLED", False)
    monkeypatch.setattr(config, "CHAT_USE_TENANT_PROFILE", False)
    monkeypatch.setattr(config, "RUNS_DIR", str(tmp_path / "runs"))
    monkeypatch.setattr(chat_router, "ensure_openai_client", lambda base_url=None: object())
    calls = []
    monkeypatch.setattr(chat_router, "answer_query_with_trace", _pipeline(calls))
    answer_cache.clear()
    answer_evidence.clear_recent()
    yield SimpleNamespace(calls=calls, chunks=chunks, client=TestClient(main.app))
    answer_cache.clear()
    answer_evidence.clear_recent()


def test_chat_writes_a_hash_chained_evidence_record_without_text(env):
    resp = env.client.post("/chat", json={"question": "有給の申請期限を教えて", "trace_id": "client-1"}).json()
    rid = resp["request_id"]
    assert rid == "srv-1" and resp["trace_id"] == "client-1"
    path = answer_evidence.evidence_path()
    raw = path.read_text(encoding="utf-8")
    assert "有給の申請期限" not in raw and "3営業日前までです。" not in raw  # hashes only
    row = json.loads(raw.splitlines()[-1])
    assert row["kind"] == "evidence" and row["request_id"] == rid and row["tenant_id"] == "default"
    assert row["answer_sha256"] == text_sha256("3営業日前までです。")
    assert row["cited"][0]["chunk_id"] == "c1" and row["cited"][0]["source_pages"] == [4]
    assert row["cited"][0]["source_row_sha256"] == text_sha256("申請は3営業日前までです。")
    assert row["selected_context_chunk_ids"] == ["c1"] and row["rag_profile_version"] == "7"
    assert row["served_from_cache"] is False and row["client_trace_id"] == "client-1"
    assert audit_log.verify_chain(path)["ok"] is True
    # Found from the file as well (another worker, or after the in-process map was cleared).
    answer_evidence.clear_recent()
    assert answer_evidence.lookup_evidence_diagnostic(rid, "default")["request_id"] == rid
    assert answer_evidence.lookup_evidence_diagnostic(rid, "other_tenant") is None


def test_chat_event_names_the_cited_documents(env, monkeypatch):
    events = []
    monkeypatch.setattr(chat_router, "append_audit_event", lambda kind, event: events.append((kind, event)))
    env.client.post("/chat", json={"question": "有給の申請期限を教えて"})
    chat_event = [e for k, e in events if k == "chat"][-1]
    assert chat_event["cited_source_docs"] == ["規程.pdf"] and "top_source_docs" in chat_event


def test_evidence_can_be_switched_off(env, monkeypatch):
    monkeypatch.setenv("AUDIT_EVIDENCE_ENABLED", "0")
    env.client.post("/chat", json={"question": "有給の申請期限を教えて"})
    assert not answer_evidence.evidence_path().exists()


# --- /chat/source -----------------------------------------------------------------------------------------------


def _source(env, **params):
    resp = env.client.get("/chat/source", params={"chunk_id": "c1", **params})
    assert resp.status_code == 200, resp.text
    return resp.json()


def test_chat_source_without_request_id_is_unchanged(env):
    body = _source(env)
    assert "stale" not in body and body["text"] == "申請は3営業日前までです。"


def test_chat_source_reports_unchanged_then_stale(env, monkeypatch):
    from rag_core import retrieval

    rid = env.client.post("/chat", json={"question": "有給の申請期限を教えて"}).json()["request_id"]
    fresh = _source(env, request_id=rid, number=1)
    assert fresh["evidence_found"] is True and fresh["cited"] is True and fresh["stale"] is False
    assert fresh["cited_index_generation"] == "gen-1"
    monkeypatch.setattr(retrieval, "chunk_row", lambda chunk_id, collection=None: {
        "id": "c1", "text": "申請は5営業日前までです。", "source_doc": "規程.pdf", "tenant_id": "default",
        "index_generation": "gen-2"})
    stale = _source(env, request_id=rid)
    assert stale["stale"] is True and stale["index_generation"] == "gen-2"


def test_chat_source_unknown_request_or_other_tenant_is_unknown(env):
    body = _source(env, request_id="no-such-request")
    assert body["evidence_found"] is False and body["stale"] is None


# --- conversations and feedback ---------------------------------------------------------------------------------


def test_conversation_turn_takes_server_evidence(env, monkeypatch):
    from webapi import conversation_store

    monkeypatch.setenv("CONVERSATION_HISTORY_ENABLED", "1")
    rid = env.client.post("/chat", json={"question": "有給の申請期限を教えて"}).json()["request_id"]
    resp = env.client.post("/chat/threads", json={
        "thread_id": "t1", "question": "q", "answer_text": "3営業日前までです。", "request_id": rid,
        "answer_mode": "made_up", "citations_count": 5, "source_docs": ["偽.pdf"]}).json()
    assert resp["provenance"] == "server"
    resp = env.client.post("/chat/threads", json={"thread_id": "t1", "question": "q2", "answer_text": "b",
                                                  "citations_count": 1, "source_docs": ["手入力.pdf"]}).json()
    assert resp["provenance"] == "client"
    turns = conversation_store.get_thread("default", "anonymous", "t1")
    assert turns[0]["source_docs"] == ["規程.pdf"] and turns[0]["answer_mode"] == "grounded"
    assert turns[0]["citations_count"] == 1 and turns[0]["provenance"] == "server"
    assert turns[1]["provenance"] == "client" and turns[1]["source_docs"] == ["手入力.pdf"]
    conversation_store.redact_source_doc("default", "規程.pdf")
    assert conversation_store.get_thread("default", "anonymous", "t1")[0]["answer_text"] == conversation_store.REDACTED_NOTICE


def test_conversation_turn_rejects_mismatched_answer(env, monkeypatch):
    monkeypatch.setenv("CONVERSATION_HISTORY_ENABLED", "1")
    rid = env.client.post("/chat", json={"question": "有給の申請期限を教えて"}).json()["request_id"]
    resp = env.client.post("/chat/threads", json={
        "thread_id": "t1", "question": "q", "answer_text": "different", "request_id": rid,
    })
    assert resp.json()["provenance"] == "client"


def test_failed_evidence_write_is_not_visible(env, monkeypatch):
    def fail(*args, **kwargs):
        raise OSError("simulated write failure")

    monkeypatch.setattr(audit_log, "_write", fail)
    before = audit_log.write_failure_count()
    assert answer_evidence.record_evidence({"request_id": "failed", "tenant_id": "default"}) is False
    assert audit_log.write_failure_count() == before + 1
    assert answer_evidence.lookup_evidence_diagnostic("failed", "default") is None


def test_conversation_server_fields_require_matching_answer(monkeypatch):
    from webapi.routers import conversations

    record = {"answer_sha256": answer_evidence.sha256_text("right"), "answer_mode": "grounded", "cited": []}
    monkeypatch.setattr(answer_evidence, "lookup_evidence", lambda request_id, tenant, **kwargs: record)
    assert conversations._server_turn_fields("id", "default", "right")["answer_mode"] == "grounded"
    assert conversations._server_turn_fields("id", "default", "wrong") is None


def test_feedback_is_linked_only_to_server_evidence_of_the_tenant(env, monkeypatch):
    monkeypatch.delenv("FEEDBACK_TOKEN_SECRET", raising=False)
    rid = env.client.post("/chat", json={"question": "有給の申請期限を教えて"}).json()["request_id"]
    base = {"feedback_token": "tok-1", "feedback_type": "good"}
    linked = env.client.post("/chat/feedback", json={**base, "request_id": rid}).json()
    unlinked = env.client.post("/chat/feedback", json={**base, "request_id": "client-made"}).json()
    assert linked["linked"] is True and unlinked["linked"] is False


# --- /chat/stream keeps the caller's audit actor ----------------------------------------------------------------


def test_stream_generation_step_sees_the_audit_actor(env, monkeypatch):
    seen = []

    def stream(question, **kwargs):
        yield "meta", {"intent": "other"}
        seen.append(audit_log._ACTOR.get())  # the generation step (after the first yield)
        ans, trace = _pipeline([])(question)
        yield "final", (ans, trace)

    monkeypatch.setattr(chat_router, "answer_query_stream", stream)
    auth = ApiAuthContext(authenticated=True, key_fingerprint="fp-stream", role="user")
    main.app.dependency_overrides[require_api_auth_rate_limited] = lambda: auth
    try:
        resp = env.client.post("/chat/stream", json={"question": "有給の申請期限を教えて"})
    finally:
        main.app.dependency_overrides.pop(require_api_auth_rate_limited, None)
    assert resp.status_code == 200
    assert seen and seen[0] and seen[0]["key_fingerprint"] == "fp-stream"
    final = [json.loads(b.split("data: ", 1)[1]) for b in resp.text.split("\n\n") if b.startswith("event: final")][0]
    assert final["request_id"] == "srv-1"
    assert answer_evidence.lookup_evidence_diagnostic("srv-1", "default")["streamed"] is True


# --- validation sees exactly the evidence the model saw ---------------------------------------------------------


def test_a_term_only_after_the_evidence_limit_does_not_validate():
    from rag_grounded import EVIDENCE_BLOCK_MAX_CHARS, build_evidence_blocks, truncate_to_evidence, validate_output

    long_chunk = Chunk("p1", "あ" * (EVIDENCE_BLOCK_MAX_CHARS + 100) + "特別休暇は五日です。", "規程.pdf", (1,),
                       meta={"index_generation": "g1"})
    answer = "- 特別休暇は五日です。[S1]"
    assert validate_output(answer, [long_chunk], "other") is True  # the full text would have "supported" it
    seen = truncate_to_evidence([long_chunk])
    assert validate_output(answer, seen, "other") is False
    assert "特別休暇" not in build_evidence_blocks([long_chunk])[0]
    assert build_evidence_blocks(seen)[0] + "…" == build_evidence_blocks([long_chunk])[0]  # same text shown
    assert seen[0].meta == {"index_generation": "g1"} and seen[0].id == "p1"
    short = Chunk("s1", "短い本文", "規程.pdf", (1,))
    assert truncate_to_evidence([short])[0] is short


@pytest.mark.parametrize('streamed', [False, True])
def test_real_pipeline_keeps_evidence_when_corpus_changes_during_generation(env, monkeypatch, streamed):
    from rag_core import retrieval
    from types import SimpleNamespace

    monkeypatch.setattr(config, 'CHAT_GENERATION_MODE', 'llm')
    monkeypatch.setattr(qa, 'guard_merged_top', lambda *a, **k: None)
    monkeypatch.setattr(qa, 'rerank_chunks', lambda question, chunks, intent=None: list(chunks))
    monkeypatch.setattr(qa, 'hybrid_retrieve', lambda question, *args, **kw: retrieval.keyword_retrieve(question, top_k=5))
    monkeypatch.setattr(chat_router, 'answer_query_with_trace', qa.answer_query_with_trace)
    monkeypatch.setattr(chat_router, 'answer_query_stream', qa.answer_query_stream)
    monkeypatch.setattr(answer_evidence, 'active_generation_id', lambda: 'new-generation')
    original = '申請は3営業日前までです。'
    output = '- 申請は3営業日前までです。[S1]\n不足: なし [S1]'
    def create(**kwargs):
        env.chunks.write_text(json.dumps({'id': 'c1', 'text': '変更後の文書', 'source_doc': '規程.pdf', 'source_pages': [4], 'index_generation': 'new-generation'}, ensure_ascii=False) + '\n', encoding='utf-8')
        if kwargs.get('stream'):
            return iter([SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content=output))])])
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=output))])
    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    monkeypatch.setattr(chat_router, 'ensure_openai_client', lambda **kwargs: client)
    response = env.client.post('/chat/stream' if streamed else '/chat', json={'question': '申請は何営業日前ですか？'})
    assert response.status_code == 200
    if streamed:
        event = next(x for x in response.text.split('\n\n') if 'event: final' in x)
        payload = json.loads(next(x[6:] for x in event.splitlines() if x.startswith('data: ')))
    else:
        payload = response.json()
    record = answer_evidence.lookup_evidence(payload['request_id'], 'default', access_scope='')
    assert record['cited'][0]['source_row_sha256'] == text_sha256(original)
    assert record['active_generation_id'] == 'gen-1'
    assert payload['citations'] and '_evidence_source_row_sha256' not in str(payload)
