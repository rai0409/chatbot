"""2026-11-22: tenant answer policy on /chat, approval status, follow-up lookups, answer cache keys and
invalidation, Japanese colloquial handling and document priority. Stubs and synthetic data only."""
from __future__ import annotations

import json
import re
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import config
from rag_core import answer_cache, approved_qa, approved_qa_exact_lookup as exact, approved_similar
from rag_core import product_profile, tenant_profile
from rag_core.question_normalization import exact_match_key
from rag_core.retrieval import RetrievedChunk
from webapi import main
from webapi.routers import chat as chat_router

REPO = Path(__file__).resolve().parents[1]


# --- helpers --------------------------------------------------------------------------------------------------------


def _fake_answer(guard_reason=None, used_fallback=False):
    ans = SimpleNamespace(
        intent="faq", guard_reason=guard_reason, used_fallback=used_fallback, citations=[],
        to_dict=lambda: {"answer_text": "回答です", "citations": [], "retrieved": [], "guard_reason": guard_reason,
                         "used_fallback": used_fallback},
    )
    trace = {
        "request_id": "req-1", "normalized_query": "q", "intent": "faq", "final_guard_reason": guard_reason,
        "final_used_fallback": used_fallback, "citations_count": 0, "latency_ms": 1, "after_rerank": [],
        "answer_mode": "fallback" if guard_reason else "grounded",
        "profile_validation": {"passed": True, "skipped": False},
    }
    return ans, trace


class _Pipeline:
    def __init__(self, result):
        self.calls = []
        self.result = result

    def __call__(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return self.result


def _mapping(tmp_path, monkeypatch, tenants, unknown="default_profile"):
    directory = tmp_path / "tenants"
    directory.mkdir(exist_ok=True)
    (directory / "default.json").write_text(json.dumps({
        "mapping_version": "1", "default_profile": "production_safe", "unknown_tenant_policy": unknown,
        "tenants": tenants,
    }), encoding="utf-8")
    monkeypatch.setattr(tenant_profile, "TENANT_PROFILE_DIR", directory)


def _tenant(name, status="active", chat_policy=None):
    entry = {"tenant_id": name, "customer_id": name, "profile": "production_safe",
             "allowed_profiles": ["production_safe"], "status": status}
    if chat_policy is not None:
        entry["chat_policy"] = chat_policy
    return entry


@pytest.fixture
def chat_env(monkeypatch, tmp_path):
    for name in ("API_AUTH_ENABLED", "API_AUTH_KEYS", "API_AUTH_TENANT_MAP", "RATE_LIMIT_ENABLED",
                 "CONVERSATION_CONTEXT_ENABLED", "ANSWER_CACHE_STATE_MEMO_MS"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(config, "CHAT_USE_TENANT_PROFILE", False)
    monkeypatch.setattr(config, "APPROVED_QA_ENABLED", False)
    monkeypatch.setattr(config, "ANSWER_CACHE_ENABLED", False)
    monkeypatch.setattr(config, "RUNS_DIR", str(tmp_path / "runs"))
    answer_cache.clear()
    pipeline = _Pipeline(_fake_answer())
    stream = _Pipeline(iter(()))
    audit = []
    monkeypatch.setattr(chat_router, "ensure_openai_client", lambda base_url=None: object())
    monkeypatch.setattr(chat_router, "answer_query_with_trace", pipeline)
    monkeypatch.setattr(chat_router, "answer_query_stream", stream)
    monkeypatch.setattr(chat_router, "append_audit_event", lambda kind, event: audit.append((kind, event)))
    _mapping(tmp_path, monkeypatch, {"default": _tenant("default")})
    yield SimpleNamespace(pipeline=pipeline, stream=stream, audit=audit, tmp=tmp_path)
    answer_cache.clear()


# --- A. tenant answer policy --------------------------------------------------------------------------------------


def test_disabled_tenant_is_refused_on_chat_stream_and_search(chat_env, monkeypatch):
    _mapping(chat_env.tmp, monkeypatch, {"default": _tenant("default"), "tenant_off": _tenant("tenant_off", "disabled")})
    client = TestClient(main.app)
    for path, body in (("/chat", {"question": "質問です", "tenant_id": "tenant_off"}),
                       ("/chat/stream", {"question": "質問です", "tenant_id": "tenant_off"}),
                       ("/search", {"query": "質問です", "tenant_id": "tenant_off"})):
        resp = client.post(path, json=body)
        assert resp.status_code == 403, path
        assert "disabled" in resp.json()["detail"]
    assert chat_env.pipeline.calls == [] and chat_env.stream.calls == []


def test_unknown_tenant_under_reject_policy_is_refused(chat_env, monkeypatch):
    _mapping(chat_env.tmp, monkeypatch, {"default": _tenant("default")}, unknown="reject")
    resp = TestClient(main.app).post("/chat", json={"question": "質問です", "tenant_id": "ghost"})
    assert resp.status_code == 403
    assert chat_env.pipeline.calls == []


def test_default_tenant_policy_keeps_legacy_behaviour(chat_env):
    policy = chat_router.resolve_chat_answer_policy("default")
    assert policy["blocked"] is False
    assert policy["allow_exact_answer"] is True
    assert policy["allow_llm_calls"] is True  # profile cost_policy only applies with CHAT_USE_TENANT_PROFILE
    resp = TestClient(main.app).post("/chat", json={"question": "質問です", "top_k": 17})
    assert resp.status_code == 200
    assert chat_env.pipeline.calls[0][1]["top_k"] == 17


def test_flag_on_applies_profile_llm_policy(chat_env, monkeypatch):
    monkeypatch.setattr(config, "CHAT_USE_TENANT_PROFILE", True)
    assert chat_router.resolve_chat_answer_policy("default")["allow_llm_calls"] is False


def test_allow_exact_answer_false_skips_approved_lookups(chat_env, monkeypatch):
    _mapping(chat_env.tmp, monkeypatch, {"default": _tenant("default", chat_policy={"allow_exact_answer": False})})
    monkeypatch.setattr(chat_router, "_approved_qa_lookup", lambda *a, **k: pytest.fail("approved lookup ran"))
    monkeypatch.setattr(chat_router, "_approved_exact_chat_lookup", lambda *a, **k: pytest.fail("exact lookup ran"))
    resp = TestClient(main.app).post("/chat", json={"question": "質問です"})
    assert resp.status_code == 200
    assert len(chat_env.pipeline.calls) == 1


def test_tenant_chat_policy_only_narrows(chat_env, monkeypatch):
    monkeypatch.setattr(config, "CHAT_USE_TENANT_PROFILE", True)
    _mapping(chat_env.tmp, monkeypatch, {"default": _tenant("default", chat_policy={"allow_llm_calls": True})})
    assert chat_router.resolve_chat_answer_policy("default")["allow_llm_calls"] is False


def test_unreadable_tenant_policy_fails_closed(chat_env, monkeypatch):
    def boom(*args, **kwargs):
        raise ValueError("mapping unreadable")

    monkeypatch.setattr(chat_router, "resolve_tenant_product_profile", boom)
    resp = TestClient(main.app).post("/chat", json={"question": "質問です"})
    assert resp.status_code == 503
    assert chat_env.pipeline.calls == []


def test_llm_forbidden_without_request_scoped_hook_fails_closed(chat_env, monkeypatch):
    from rag_core import llm

    _mapping(chat_env.tmp, monkeypatch, {"default": _tenant("default", chat_policy={"allow_llm_calls": False})})
    monkeypatch.delattr(llm, "extractive_only", raising=False)
    monkeypatch.setattr(chat_router, "_llm_mode_for", lambda tenant: "llm")
    resp = TestClient(main.app).post("/chat", json={"question": "質問です"})
    assert resp.status_code == 503
    assert resp.json()["error_type"] == "llm_not_allowed_for_tenant"
    assert chat_env.pipeline.calls == []


def test_llm_forbidden_uses_extractive_hook_and_no_client(chat_env, monkeypatch):
    from rag_core import llm

    entered = []

    @contextmanager
    def extractive_only():
        entered.append(True)
        yield

    _mapping(chat_env.tmp, monkeypatch, {"default": _tenant("default", chat_policy={"allow_llm_calls": False})})
    monkeypatch.setattr(llm, "extractive_only", extractive_only, raising=False)
    monkeypatch.setattr(config, "CHAT_GENERATION_MODE", "llm")
    resp = TestClient(main.app).post("/chat", json={"question": "質問です"})
    assert resp.status_code == 200
    assert entered == [True]
    assert chat_env.pipeline.calls[0][1]["client"] is None
    event = [e for k, e in chat_env.audit if k == "chat"][-1]
    assert event["llm_allowed"] is False


def test_low_confidence_answer_is_marked_for_review_and_not_cached(chat_env, monkeypatch):
    monkeypatch.setattr(config, "ANSWER_CACHE_ENABLED", True)
    chat_env.pipeline.result = _fake_answer(guard_reason="low_score", used_fallback=True)
    resp = TestClient(main.app).post("/chat", json={"question": "質問です"})
    body = resp.json()
    assert body["review_required"] is True and body["review_reason"] == "low_confidence"
    event = [e for k, e in chat_env.audit if k == "chat"][-1]
    assert event["review_required"] is True
    assert answer_cache.size() == 0


def test_grounded_answer_is_not_marked(chat_env):
    body = TestClient(main.app).post("/chat", json={"question": "質問です"}).json()
    assert "review_required" not in body


# --- C. follow-up questions ---------------------------------------------------------------------------------------


def test_approved_lookup_uses_the_contextualized_question(chat_env, monkeypatch):
    seen = []
    monkeypatch.setattr(chat_router, "_conversation_question",
                        lambda req, auth, tenant: ("有給の申請方法は？", {"original_question": req.question}))
    monkeypatch.setattr(chat_router, "_approved_qa_lookup", lambda q, tenant_id="default": seen.append(q) or None)
    monkeypatch.setattr(chat_router, "_approved_exact_chat_lookup", lambda q, tenant: seen.append(q) or None)
    TestClient(main.app).post("/chat", json={"question": "申請方法は？"})
    assert seen == ["有給の申請方法は？", "有給の申請方法は？"]


# --- B. approval status ----------------------------------------------------------------------------------------------


def _write_exact_index(path, qa_id="qa-1", answer="承認回答", source_doc="faq.pdf"):
    key = exact_match_key("営業時間は？")
    item = {"id": f"approved_qa_pair:{qa_id}", "metadata": {
        "id": f"approved_qa_pair:{qa_id}", "qa_id": qa_id, "tenant_id": "default", "question_text": "営業時間は？",
        "approved_answer": answer, "approved_citations": [{"source_doc": source_doc, "source_pages": [1]}]}}
    path.write_text(json.dumps({key: [item]}, ensure_ascii=False), encoding="utf-8")
    return path


def _write_live(path, status="approved", answer="承認回答"):
    record = {"qa_id": "qa-1", "question": "営業時間を教えて", "approved_answer": answer, "status": status,
              "tenant_id": "default", "approved_citations": [{"source_doc": "faq.pdf", "source_pages": [1]}]}
    path.write_text(json.dumps(record, ensure_ascii=False) + "\n", encoding="utf-8")


@pytest.mark.parametrize("status,answer,served", [
    ("approved", "承認回答", True),
    ("rejected", "承認回答", False),
    ("draft", "承認回答", False),
    ("approved", "改訂後の回答", False),  # re-approved with another answer: the offline index is stale
])
def test_exact_index_hit_must_be_live_approved(monkeypatch, tmp_path, status, answer, served):
    monkeypatch.setenv(exact.EXACT_INDEX_PATH_ENV, str(_write_exact_index(tmp_path / "exact.json")))
    live = tmp_path / "live.jsonl"
    _write_live(live, status=status, answer=answer)
    monkeypatch.setattr(config, "APPROVED_QA_ENABLED", True)
    monkeypatch.setattr(config, "APPROVED_QA_PATH", str(live))
    monkeypatch.setattr(chat_router, "_approved_qa_cache", {})
    payload = chat_router._approved_exact_chat_lookup("営業時間は？", "default")
    assert (payload is not None) is served


def test_exact_hit_with_missing_source_document_is_dropped(monkeypatch, tmp_path):
    index = _write_exact_index(tmp_path / "exact.json")
    exact.set_source_doc_exists_check(lambda tenant, doc: doc != "faq.pdf")
    try:
        assert exact.lookup_approved_qa_exact("営業時間は？", tenant_id="default", index_path=index) == []
    finally:
        exact.set_source_doc_exists_check(None)
    assert exact.lookup_approved_qa_exact("営業時間は？", tenant_id="default", index_path=index)


def test_similar_search_requires_status_and_live_ids(monkeypatch):
    rows = [
        {"qa_id": "qa-live", "status": "approved"},
        {"qa_id": "qa-stale", "status": "approved"},
        {"qa_id": "qa-rejected", "status": "rejected"},
    ]
    metas = [{**r, "question_text": "質問", "answer_text": "回答", "doc_type": "approved_qa_pair",
              "chunk_type": "qa_pair", "tenant_id": "default"} for r in rows]

    class Collection:
        where = None

        def query(self, **kwargs):
            Collection.where = kwargs["where"]
            return {"ids": [[m["qa_id"] for m in metas]], "documents": [["Q: 質問\nA: 回答"] * 3], "metadatas": [metas], "distances": [[0.1] * 3]}

    monkeypatch.setattr(approved_similar.embedder, "embed_queries", lambda queries, client=None: [[0.0, 1.0]])
    monkeypatch.delenv("APPROVED_SIMILAR_REQUIRE_STATUS", raising=False)
    found = approved_similar.search_approved_similar_candidates(
        "質問", collection=Collection(), top_k=5, live_qa_ids=frozenset({"qa-live", "qa-rejected"}))
    assert [c["qa_id"] for c in found] == ["qa-live"]
    assert {"status": "approved"} in Collection.where["$and"]
    monkeypatch.setenv("APPROVED_SIMILAR_REQUIRE_STATUS", "false")
    assert {"status": "approved"} not in approved_similar._qa_pair_where("default")["$and"]


def test_pair_chunk_rows_carry_the_approved_status(tmp_path):
    from scripts.approved_qa_to_pair_chunks import convert_approved_qa_to_pair_chunks

    source = tmp_path / "approved.jsonl"
    _write_live(source)
    out = tmp_path / "pairs.jsonl"
    summary = convert_approved_qa_to_pair_chunks(input_path=source, output_path=out)
    rows = [json.loads(line) for line in out.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert rows and all(row["status"] == "approved" for row in rows)
    assert summary["status_stamped_records"] == len(rows)


def test_store_changes_push_invalidate_the_tenant_cache(monkeypatch, tmp_path):
    from rag_core import approved_qa_store as store

    monkeypatch.setattr(config, "APPROVED_QA_PATH", str(tmp_path / "approved" / "live.jsonl"))
    monkeypatch.delenv("APPROVED_QA_REQUIRE_APPROVER", raising=False)
    cleared = []
    monkeypatch.setattr(answer_cache, "clear_tenant", lambda tenant: cleared.append(tenant) or 0)
    who = {"name": "山田太郎", "id": "fp-1", "verified": True}
    record = store.save({"tenant_id": "tenant_a", "question": "申請期限はいつですか？", "approved_answer": "毎月末日です。",
                         "citations": [{"source_doc": "manual.pdf", "source_pages": [2]}]}, by=who, reason="新規")
    store.set_status("tenant_a", record["qa_id"], "rejected", by=who, reason="却下")
    store.delete("tenant_a", record["qa_id"], by=who, reason="削除")
    assert cleared == ["tenant_a", "tenant_a", "tenant_a"]


# --- D. answer cache --------------------------------------------------------------------------------------------------


@pytest.fixture
def cache_state(monkeypatch, tmp_path):
    chunks = tmp_path / "chunks.jsonl"
    chunks.write_text('{"id":"c1","text":"本文"}\n', encoding="utf-8")
    monkeypatch.setattr(config, "CHUNKS_JSONL_PATH", str(chunks))
    monkeypatch.setattr(config, "APPROVED_QA_ENABLED", False)
    monkeypatch.setattr(config, "ANSWER_CACHE_ENABLED", True)
    monkeypatch.delenv("ANSWER_CACHE_STATE_MEMO_MS", raising=False)
    monkeypatch.delenv("ANSWER_CACHE_BACKEND", raising=False)
    answer_cache.clear()
    yield tmp_path
    answer_cache.clear()


def _key(question="質問です", tenant="default", **kw):
    return answer_cache.build_key(question, top_k=5, max_context_chars=8000, tenant_id=tenant, **kw)


def test_long_vowel_variants_share_a_key(cache_state):
    assert _key("サーバの設定") == _key("サーバーの設定")
    assert _key("ユーザ登録") == _key("ユーザー登録")
    assert _key("ビールの申請") != _key("ビルの申請")


def test_principal_digest_separates_keys(cache_state):
    assert _key(principal_digest="") != _key(principal_digest="groups:abc")


def test_product_policy_files_change_the_key(cache_state, monkeypatch):
    profiles = cache_state / "profiles"
    profiles.mkdir()
    target = profiles / "production_safe.json"
    target.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(product_profile, "PRODUCT_PROFILE_DIR", profiles)
    before = _key()
    target.write_text('{"edited": true}', encoding="utf-8")
    assert _key() != before


def test_tenant_flag_and_preprocess_setting_are_in_the_pipeline_state(cache_state, monkeypatch):
    monkeypatch.setattr(config, "CHAT_USE_TENANT_PROFILE", False)
    off = answer_cache._pipeline_state()
    monkeypatch.setattr(config, "CHAT_USE_TENANT_PROFILE", True)
    assert answer_cache._pipeline_state() != off
    monkeypatch.setattr(config, "QUERY_PREPROCESS_ENABLED", False, raising=False)
    assert False in answer_cache._pipeline_state()


def test_clear_tenant_removes_only_that_tenant(cache_state):
    answer_cache.put(_key(tenant="tenant_a"), {"answer_text": "a"})
    answer_cache.put(_key(tenant="tenant_b"), {"answer_text": "b"})
    assert answer_cache.clear_tenant("tenant_a") == 1
    assert answer_cache.get(_key(tenant="tenant_a")) is None
    assert answer_cache.get(_key(tenant="tenant_b")) == {"answer_text": "b"}


def test_redis_keys_are_grouped_by_tenant(cache_state):
    key = _key(tenant="tenant_a")
    assert re.fullmatch(r"ac:tenant_a:[0-9a-f]{64}", answer_cache._redis_key(key))
    assert answer_cache._redis_key(("a*b",))[: len("ac:a_b:")] == "ac:a_b:"


def test_state_memo_holds_within_its_window(cache_state, monkeypatch):
    calls = []
    monkeypatch.setattr(answer_cache, "_corpus_state_uncached", lambda: calls.append(1) or ("state",))
    monkeypatch.setenv("ANSWER_CACHE_STATE_MEMO_MS", "60000")
    answer_cache.clear_state_memo()
    answer_cache._corpus_state()
    answer_cache._corpus_state()
    assert len(calls) == 1
    monkeypatch.setenv("ANSWER_CACHE_STATE_MEMO_MS", "0")
    answer_cache._corpus_state()
    assert len(calls) == 2
    answer_cache.clear_state_memo()


def test_answer_is_not_cached_while_keyword_index_refreshes(chat_env, cache_state, monkeypatch):
    from rag_core import retrieval

    monkeypatch.setattr(retrieval, "keyword_index_refreshing", lambda: True, raising=False)
    client = TestClient(main.app)
    client.post("/chat", json={"question": "質問です"})
    assert answer_cache.size() == 0
    monkeypatch.setattr(retrieval, "keyword_index_refreshing", lambda: False, raising=False)
    client.post("/chat", json={"question": "質問です"})
    assert answer_cache.size() == 1


def test_non_default_rag_profile_is_cacheable(chat_env, cache_state, monkeypatch):
    monkeypatch.setenv("RAG_PROFILE_TENANT_MAP", "default=acme_profile")
    client = TestClient(main.app)
    client.post("/chat", json={"question": "質問です", "rag_profile_id": "acme_profile"})
    client.post("/chat", json={"question": "質問です", "rag_profile_id": "acme_profile"})
    assert len(chat_env.pipeline.calls) == 1


# --- E. Japanese colloquial handling ---------------------------------------------------------------------------------


def test_colloquial_definition_questions_are_definition_type():
    from rag_core.question_types import detect_question_type

    for question in ("VPNってなに", "VPNって何ですか", "RAGとは何", "この略語はどういう意味"):
        assert detect_question_type(question) == "definition", question


def test_colloquial_rules_rewrite_before_strip_and_accept_ascii_tilde():
    data = json.loads((REPO / "configs" / "query_rules" / "colloquial_ja.json").read_text(encoding="utf-8"))
    suffix = data["strip_suffix"]
    rewrites = [i for i, r in enumerate(suffix) if isinstance(r, dict) and r.get("type") == "rewrite"]
    tte = next(i for i, r in enumerate(suffix) if isinstance(r, str) and "って(?=" in r)
    assert rewrites and max(rewrites) < tte
    rule = suffix[rewrites[0]]
    assert re.sub(rule["pattern"], rule["replace"], "vpnってなに") == "vpnとは"
    assert re.sub(rule["pattern"], rule["replace"], "vpnってなんですか?") == "vpnとは"
    assert any(isinstance(r, str) and "〜|~" in r for r in suffix)


def test_exact_key_long_vowel_rule():
    assert exact_match_key("サーバー") == exact_match_key("サーバ")
    assert exact_match_key("ユーザーの登録") == exact_match_key("ユーザの登録")
    assert exact_match_key("ビール") != exact_match_key("ビル")


def test_colloquial_strong_map_is_built_once_per_index(monkeypatch, tmp_path):
    from rag_core import query_preprocess

    rules = tmp_path / "rules"
    rules.mkdir()
    (rules / "colloquial_ja.json").write_text(json.dumps({"strip_prefix": ["^あの、"], "strip_suffix": []}), encoding="utf-8")
    monkeypatch.setattr(query_preprocess, "RULES_DIR", rules)
    path = tmp_path / "qa.jsonl"
    record = {"qa_id": "one", "question": "営業時間", "approved_answer": "9時から", "status": "approved",
              "tenant_id": "default", "approved_citations": [{"source_doc": "faq.pdf", "source_pages": [1]}]}
    path.write_text(json.dumps(record, ensure_ascii=False) + "\n", encoding="utf-8")
    index = approved_qa.load_approved_qa(path)
    builds = []
    original = approved_qa._strong_key_map
    monkeypatch.setattr(approved_qa, "_strong_key_map", lambda idx: builds.append(1) or original(idx))
    for _ in range(3):
        assert approved_qa.lookup_approved_answer(index, "あの、営業時間").qa_id == "one"
    assert len(builds) == 1


def test_similar_scoring_uses_configured_synonyms(monkeypatch):
    monkeypatch.setattr(approved_similar, "_loaded_synonym_groups",
                        lambda profile_id="default": ([["保険証", "被保険者証"]], ("v1",)))
    monkeypatch.setenv("APPROVED_SIMILAR_BUILTIN_SYNONYMS", "false")
    mapping = approved_similar._synonym_map()
    assert "被保険者証" in mapping["保険証"]
    assert "設問" not in mapping
    monkeypatch.setenv("APPROVED_SIMILAR_BUILTIN_SYNONYMS", "true")
    assert "設問" in approved_similar._synonym_map()


# --- F. document priority --------------------------------------------------------------------------------------------


def test_priority_metadata_is_normalized_and_optional():
    from rag_core.canonical_metadata import normalize_source_metadata
    from rag_core.source_metadata import normalize_source_metadata as normalize_source

    out = normalize_source_metadata({"source_doc": "規程.pdf", "doc_priority": "5", "authority": " 人事部 ",
                                     "effective_from": "2026/04/01"})
    assert out["doc_priority"] == 5 and out["authority"] == "人事部" and out["effective_from"] == "2026-04-01"
    plain = normalize_source_metadata({"source_doc": "a.pdf"})
    assert "doc_priority" not in plain and "authority" not in plain
    bad = normalize_source_metadata({"source_doc": "a.pdf", "doc_priority": "high", "effective_from": "someday"})
    assert "doc_priority" not in bad and "effective_from" not in bad
    assert normalize_source({"source_doc": "a.pdf", "doc_priority": 3})["doc_priority"] == 3


def _chunk(doc, score, **meta):
    return RetrievedChunk(text=f"{doc} の本文", metadata={"source_doc": doc, **meta}, score=score)


def test_apply_doc_priority_moves_preferred_documents_up():
    from rag_core.profile_engine import apply_doc_priority

    chunks = [_chunk("memo_1.pdf", 0.1), _chunk("memo_2.pdf", 0.2), _chunk("規程_就業.pdf", 0.3)]
    profile = {"doc_boost_rules": [{"rule_id": "regulations", "points": 100, "match": {"source_doc": "規程_*.pdf"}}]}
    ranked, trace = apply_doc_priority(chunks, profile)
    assert [c.metadata["source_doc"] for c in ranked] == ["規程_就業.pdf", "memo_1.pdf", "memo_2.pdf"]
    assert [c.score for c in ranked] == [0.1, 0.2, 0.3]  # scores stay ascending in the new order
    assert trace["reordered"] is True and trace["prioritized_chunks"] == 1
    same, _ = apply_doc_priority(chunks, {})
    assert same == chunks


def test_doc_rules_match_authority_and_metadata_priority():
    from rag_core.profile_engine import apply_doc_priority, doc_rule_matches

    assert doc_rule_matches({"match": {"authority": "人事部"}}, {"authority": "人事部"})
    assert not doc_rule_matches({"match": {"typo": "x"}}, {"source_doc": "a.pdf"})
    chunks = [_chunk("a.pdf", 0.1), _chunk("b.pdf", 0.2, doc_priority=60)]
    ranked, _ = apply_doc_priority(chunks, None)
    assert [c.metadata["source_doc"] for c in ranked] == ["b.pdf", "a.pdf"]


def test_profile_matching_uses_the_index_normalization(monkeypatch):
    from rag_core import profile_engine
    from rag_core.ja_text import normalize_for_index

    assert profile_engine.normalize_text("ＶＰＮ　接続") == normalize_for_index("ＶＰＮ　接続")
