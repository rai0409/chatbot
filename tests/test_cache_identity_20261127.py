"""2026-11-27: answer-cache identity — the caller's access scope and the tenant LLM policy are part of the key, a hit
gets a new request id and keeps its origin internal. Stubs and synthetic data only."""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import config
from rag_core import answer_cache, answer_evidence, mail_access
from schemas import AnswerResult, CitationOut
from webapi import main
from webapi.rate_limit import require_api_auth_rate_limited
from webapi.routers import chat as chat_router


def _pipeline(calls):
    def fake(question, **kwargs):
        calls.append(question)
        ans = AnswerResult(
            answer_text="回答本文 [S1]", answer_with_footnotes="回答本文", intent="other", guard_reason=None,
            used_fallback=False,
            citations=[CitationOut(number=1, source_doc="規程.pdf", source_pages=[2], chunk_id="c1",
                                   member_chunk_ids=["c1"], chunk_sha256="ab" * 32, index_generation="old-generation")],
        )
        trace = {"request_id": f"srv-{len(calls)}", "normalized_query": question, "intent": "other",
                 "final_guard_reason": None, "final_used_fallback": False, "citations_count": 1, "latency_ms": 1,
                 "after_rerank": [], "answer_mode": "grounded", "selected_context_chunk_ids": ["c1", "c2"],
                 "profile_validation": {"passed": True, "skipped": False},
                 "cited": [{"chunk_id": "c1", "source_row_sha256": answer_evidence.sha256_text("original"), "index_generation": "old-generation"}],
                 "active_generation_id": "old-generation"}
        return ans, trace

    return fake


@pytest.fixture
def env(monkeypatch, tmp_path):
    for name in ("API_AUTH_ENABLED", "API_AUTH_KEYS", "API_AUTH_TENANT_MAP", "RATE_LIMIT_ENABLED",
                 "CONVERSATION_CONTEXT_ENABLED", "ANSWER_CACHE_STATE_MEMO_MS", "ANSWER_CACHE_BACKEND",
                 "RETRIEVAL_ACL_SCOPED"):
        monkeypatch.delenv(name, raising=False)
    chunks = tmp_path / "chunks.jsonl"
    chunks.write_text(json.dumps({"id": "c1", "text": "本文", "source_doc": "規程.pdf"}, ensure_ascii=False) + "\n",
                      encoding="utf-8")
    monkeypatch.setattr(config, "CHUNKS_JSONL_PATH", str(chunks))
    monkeypatch.setattr(config, "APPROVED_QA_ENABLED", False)
    monkeypatch.setattr(config, "ANSWER_CACHE_ENABLED", True)
    monkeypatch.setattr(config, "CHAT_USE_TENANT_PROFILE", False)
    monkeypatch.setattr(config, "RUNS_DIR", str(tmp_path / "runs"))
    monkeypatch.setattr(chat_router, "ensure_openai_client", lambda base_url=None: object())
    calls = []
    monkeypatch.setattr(chat_router, "answer_query_with_trace", _pipeline(calls))
    answer_cache.clear()
    answer_evidence.clear_recent()
    caller = {"auth": None}
    main.app.dependency_overrides[require_api_auth_rate_limited] = lambda: caller["auth"]
    yield SimpleNamespace(calls=calls, caller=caller, client=TestClient(main.app))
    main.app.dependency_overrides.pop(require_api_auth_rate_limited, None)
    answer_cache.clear()
    answer_evidence.clear_recent()


def _as(env, principals):
    env.caller["auth"] = SimpleNamespace(authenticated=True, key_fingerprint="fp-" + "-".join(principals or ["none"]),
                                         role="user", acl_principals=frozenset(principals or ()))


def _ask(env):
    resp = env.client.post("/chat", json={"question": "有給の申請期限は？"})
    assert resp.status_code == 200, resp.text
    return resp.json()


def test_different_access_scopes_do_not_share_a_cache_entry(env, monkeypatch):
    monkeypatch.setattr(mail_access, "MAIL_SEARCH_ENABLED", True)
    _as(env, ["group-a"])
    _ask(env)
    _as(env, ["group-b"])
    _ask(env)
    assert len(env.calls) == 2  # the second scope ran its own retrieval
    _as(env, ["group-a"])
    hit = _ask(env)
    assert len(env.calls) == 2 and hit["request_id"] not in {"srv-1", "srv-2"}  # the same scope shares its entry
    assert answer_cache.size() == 2


def test_without_acl_scoping_the_tenant_shares_one_entry(env):
    _as(env, ["group-a"])
    _ask(env)
    _as(env, ["group-b"])
    _ask(env)
    assert len(env.calls) == 1 and answer_cache.size() == 1


def test_acl_scoped_caller_without_principals_is_never_cached(env, monkeypatch):
    monkeypatch.setenv("RETRIEVAL_ACL_SCOPED", "1")
    _as(env, [])
    _ask(env)
    _ask(env)
    assert len(env.calls) == 2 and answer_cache.size() == 0


def test_access_scope_digest_contract(monkeypatch):
    monkeypatch.delenv("RETRIEVAL_ACL_SCOPED", raising=False)
    monkeypatch.setattr(mail_access, "MAIL_SEARCH_ENABLED", False)
    assert answer_cache.access_scope_digest(SimpleNamespace(acl_principals={"x"})) == ""
    monkeypatch.setattr(mail_access, "MAIL_SEARCH_ENABLED", True)
    assert answer_cache.access_scope_digest(object()) is None
    one = answer_cache.access_scope_digest(SimpleNamespace(groups=["b", "a"]))
    assert one == answer_cache.access_scope_digest(SimpleNamespace(acl_principals=("a", "b")))
    assert one.startswith("acl:")
    assert one != answer_cache.access_scope_digest(SimpleNamespace(groups=["a"]))


def test_cache_hit_gets_a_new_request_id_and_references_its_origin(env):
    _as(env, ["group-a"])
    first = _ask(env)
    second = _ask(env)
    assert len(env.calls) == 1
    assert first["request_id"] == "srv-1" and second["request_id"] not in {"srv-1", None}
    assert "_origin" not in first and "_origin" not in second
    evidence = answer_evidence.lookup_evidence_diagnostic(second["request_id"], "default")
    assert evidence["served_from_cache"] is True and evidence["origin_request_id"] == "srv-1"
    assert evidence["selected_context_chunk_ids"] == ["c1", "c2"]
    assert evidence["cache_key_digest"] == answer_evidence.lookup_evidence_diagnostic("srv-1", "default")["cache_key_digest"]


def test_client_trace_id_is_kept_apart_from_the_request_id(env):
    _as(env, ["group-a"])
    resp = env.client.post("/chat", json={"question": "有給の申請期限は？", "trace_id": "client-trace"}).json()
    assert resp["trace_id"] == "client-trace" and resp["request_id"] == "srv-1"
    assert answer_evidence.lookup_evidence_diagnostic("client-trace", "default") is None
    assert answer_evidence.lookup_evidence_diagnostic("srv-1", "default")["client_trace_id"] == "client-trace"


def test_tenant_llm_policy_is_part_of_the_key(env):
    base = dict(api_auth=None, top_k=5, max_context_chars=8000, tenant_id="default", rag_profile_id="default")
    allowed = chat_router._chat_cache_key("質問", answer_policy={"allow_llm_calls": True}, **base)
    forbidden = chat_router._chat_cache_key("質問", answer_policy={"allow_llm_calls": False}, **base)
    assert allowed != forbidden
    assert allowed[-2:] == chat_router._ANSWER_CACHE_NAMESPACE


def test_cache_evidence_preserves_original_row_and_generation(env, monkeypatch):
    from rag_core import retrieval
    _as(env, ["group-a"])
    state = {"text": "old", "generation": "gen-old"}
    monkeypatch.setattr(retrieval, "chunk_row", lambda *a, **k: {"text": state["text"]})
    monkeypatch.setattr(answer_evidence, "active_generation_id", lambda: state["generation"])
    first = _ask(env)
    original = answer_evidence.lookup_evidence_diagnostic(first["request_id"], "default")
    state.update(text="new", generation="gen-new")
    second = _ask(env)
    hit = answer_evidence.lookup_evidence_diagnostic(second["request_id"], "default")
    assert len(env.calls) == 1
    assert hit["cited"] == original["cited"]
    assert original["cited"][0]["source_row_sha256"] == answer_evidence.sha256_text("original")
    assert hit["active_generation_id"] == original["active_generation_id"] == "old-generation"


def _stream_body(env):
    response = env.client.post('/chat/stream', json={'question': '有給の申請期限は？'})
    assert response.status_code == 200
    events = [part for part in response.text.split('\n\n') if 'event: final' in part]
    assert len(events) == 1
    return json.loads(next(line[6:] for line in events[0].splitlines() if line.startswith('data: ')))


@pytest.mark.parametrize('first_stream,second_stream', [(False, True), (True, False), (False, False)])
def test_cross_transport_snapshot(env, monkeypatch, first_stream, second_stream):
    from rag_core import retrieval
    _as(env, ['group-a'])
    pipeline = _pipeline(env.calls)
    def stream(question, **kwargs):
        yield 'final', pipeline(question, **kwargs)
    monkeypatch.setattr(chat_router, 'answer_query_stream', stream)
    monkeypatch.setattr(retrieval, 'chunk_row', lambda *a, **k: {'text': 'original'})
    monkeypatch.setattr(answer_evidence, 'active_generation_id', lambda: 'old-generation')
    first = _stream_body(env) if first_stream else _ask(env)
    original = answer_evidence.lookup_evidence_diagnostic(first['request_id'], 'default')
    def unexpected(*a, **k):
        raise AssertionError('cache hit must not re-fetch evidence')
    monkeypatch.setattr(retrieval, 'chunk_row', unexpected)
    monkeypatch.setattr(answer_evidence, 'active_generation_id', unexpected)
    second = _stream_body(env) if second_stream else _ask(env)
    hit = answer_evidence.lookup_evidence_diagnostic(second['request_id'], 'default')
    assert len(env.calls) == 1
    assert hit['cited'] == original['cited']
    assert original['cited'][0]['source_row_sha256'] == answer_evidence.sha256_text('original')
    assert hit['active_generation_id'] == hit['origin_generation_id'] == 'old-generation'
    assert hit['origin_request_id'] == first['request_id'] != second['request_id']
    assert hit['streamed'] is second_stream and '_origin' not in second


@pytest.mark.parametrize('kind', ['missing', 'legacy', 'answer', 'tenant', 'digest', 'schema'])
def test_bad_origin_is_a_cache_miss(env, kind):
    _as(env, ['group-a'])
    _ask(env)
    key = next(iter(answer_cache._CACHE))
    entry = answer_cache.get(key)
    origin = entry['_origin']
    if kind == 'missing':
        del entry['_origin']
    elif kind == 'legacy':
        entry['_origin'] = {'request_id': 'legacy'}
    elif kind == 'answer':
        entry['answer_text'] = 'tampered'
    elif kind == 'tenant':
        origin['evidence']['tenant_id'] = 'other'
    elif kind == 'digest':
        origin['evidence']['cache_key_digest'] = 'other'
    else:
        del origin['evidence']['cited']
    answer_cache.put(key, entry)
    _ask(env)
    assert len(env.calls) == 2


def test_failed_save_and_disabled_transition(env, monkeypatch):
    from rag_core import audit_log
    _as(env, ['group-a'])
    original_write = audit_log.append_evidence_audit_event
    monkeypatch.setattr(audit_log, 'append_evidence_audit_event', lambda record: False)
    failed = _ask(env)
    assert answer_cache.size() == 0
    assert answer_evidence.lookup_evidence_diagnostic(failed['request_id'], 'default') is None
    monkeypatch.setenv('AUDIT_EVIDENCE_ENABLED', '0')
    _ask(env)
    _ask(env)
    assert len(env.calls) == 2 and answer_cache.size() == 1
    monkeypatch.setenv('AUDIT_EVIDENCE_ENABLED', '1')
    monkeypatch.setattr(audit_log, 'append_evidence_audit_event', original_write)
    _ask(env)
    assert len(env.calls) == 3
    hit_write = audit_log.append_evidence_audit_event
    monkeypatch.setattr(audit_log, 'append_evidence_audit_event', lambda record: False)
    hit = _ask(env)
    assert len(env.calls) == 3 and answer_evidence.lookup_evidence_diagnostic(hit['request_id'], 'default') is None
    monkeypatch.setattr(audit_log, 'append_evidence_audit_event', hit_write)


def test_local_snapshot_does_not_share_nested_references(env):
    _as(env, ['group-a'])
    _ask(env)
    key = next(iter(answer_cache._CACHE))
    entry = answer_cache.get(key)
    entry['_origin']['evidence']['selected_context_chunk_ids'].append('tampered')
    assert 'tampered' not in answer_cache.get(key)['_origin']['evidence']['selected_context_chunk_ids']


def test_stream_final_is_saved_before_consumer_stops(env, monkeypatch):
    import asyncio
    from webapi.api_auth import ApiAuthContext
    pipeline = _pipeline(env.calls)
    def stream(question, **kwargs):
        yield 'final', pipeline(question, **kwargs)
    monkeypatch.setattr(chat_router, 'answer_query_stream', stream)
    response = chat_router.chat_stream(chat_router.ChatRequest(question='有給の申請期限は？'),
                                      api_auth=ApiAuthContext(authenticated=True, key_fingerprint='fp', role='user'))
    async def check():
        iterator = response.body_iterator
        final = await iterator.__anext__()
        assert 'event: final' in final
        data = json.loads(final.split('data: ', 1)[1])
        assert answer_evidence.lookup_evidence_diagnostic(data['request_id'], 'default')['streamed'] is True
        await iterator.aclose()
    asyncio.run(check())


def test_redis_snapshot_round_trip(env, monkeypatch):
    from webapi import state_backend
    _as(env, ['group-a'])
    _ask(env)
    key = next(iter(answer_cache._CACHE))
    entry = answer_cache.get(key)
    class Redis:
        data = {}
        def set(self, key, value, ex):
            self.data[key] = value
        def get(self, key):
            return self.data.get(key)
    redis = Redis()
    monkeypatch.setenv('ANSWER_CACHE_BACKEND', 'redis')
    monkeypatch.setattr(state_backend, 'redis_client', lambda: redis)
    answer_cache.put(key, entry)
    assert answer_cache.get(key) == entry
    assert answer_evidence.valid_cache_origin(answer_cache.get(key)['_origin'], payload=entry,
                                               tenant_id='default', cache_key_digest=answer_cache.key_digest(key),
                                               access_scope=answer_cache.access_scope_digest(env.caller['auth']))
    monkeypatch.delenv('ANSWER_CACHE_BACKEND')


def test_stream_failed_save_does_not_cache(env, monkeypatch):
    from rag_core import audit_log
    _as(env, ['group-a'])
    pipeline = _pipeline(env.calls)
    def stream(question, **kwargs):
        yield 'final', pipeline(question, **kwargs)
    monkeypatch.setattr(chat_router, 'answer_query_stream', stream)
    monkeypatch.setattr(audit_log, 'append_evidence_audit_event', lambda record: False)
    final = _stream_body(env)
    assert answer_cache.size() == 0
    assert answer_evidence.lookup_evidence_diagnostic(final['request_id'], 'default') is None
    _stream_body(env)
    assert len(env.calls) == 2


def test_router_evidence_scope_is_enforced_for_all_consumers(env, monkeypatch):
    monkeypatch.setattr(mail_access, 'MAIL_SEARCH_ENABLED', True)
    monkeypatch.setenv('CONVERSATION_HISTORY_ENABLED', '1')
    monkeypatch.delenv('FEEDBACK_TOKEN_SECRET', raising=False)
    _as(env, ['group-a'])
    answer = _ask(env)
    rid = answer['request_id']
    source = env.client.get('/chat/source', params={'chunk_id': 'c1', 'request_id': rid}).json()
    assert source['evidence_found'] is True
    _as(env, ['group-b'])
    source = env.client.get('/chat/source', params={'chunk_id': 'c1', 'request_id': rid}).json()
    assert source['evidence_found'] is False and source['stale'] is None
    turn = env.client.post('/chat/threads', json={'thread_id': 'scope', 'question': 'q', 'answer_text': answer['answer_text'], 'request_id': rid}).json()
    assert turn['provenance'] == 'client'
    feedback = env.client.post('/chat/feedback', json={'feedback_token': 'tok', 'feedback_type': 'good', 'request_id': rid}).json()
    assert feedback['linked'] is False


def test_cache_rejects_changed_citation_and_scope(env):
    _as(env, ['group-a'])
    _ask(env)
    key = next(iter(answer_cache._CACHE))
    entry = answer_cache.get(key)
    scope = answer_cache.access_scope_digest(env.caller['auth'])
    args = dict(payload=entry, tenant_id='default', cache_key_digest=answer_cache.key_digest(key), access_scope=scope)
    assert answer_evidence.valid_cache_origin(entry['_origin'], **args)
    args['access_scope'] = 'acl:different'
    assert not answer_evidence.valid_cache_origin(entry['_origin'], **args)
    args['access_scope'] = scope
    entry['citations'][0]['source_doc'] = '別文書.pdf'
    assert not answer_evidence.valid_cache_origin(entry['_origin'], **args)
