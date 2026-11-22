import json

import pytest

from rag_core.question_normalization import exact_match_key
from rag_core import approved_qa_exact_lookup as lookup, answer_cache


@pytest.mark.parametrize("a,b", [("3.5日", "35日"), ("第1-2項", "第12項"), ("ビール", "ビル")])
def test_g3_meaningful_characters_do_not_collide(a, b):
    assert exact_match_key(a) != exact_match_key(b)


def test_g3_legacy_index_revalidates_item_question(tmp_path):
    old_key = exact_match_key("有給は35日")
    path = tmp_path / "old.json"
    path.write_text(json.dumps({old_key: [{"metadata": {"id": "decimal", "question_text": "有給は3.5日", "tenant_id": "default"}}, {"metadata": {"id": "integer", "question_text": "有給は35日", "tenant_id": "default"}}]}, ensure_ascii=False), encoding="utf-8")
    hits = lookup.lookup_approved_qa_exact("有給は35日", tenant_id="default", index_path=path)
    assert [h["metadata"]["id"] for h in hits] == ["integer"]
    hits = lookup.lookup_approved_qa_exact("有給は3.5日", tenant_id="default", index_path=path)
    assert [h["metadata"]["id"] for h in hits] == ["decimal"]


def test_g4_setting_changes_local_and_redis_key(monkeypatch):
    monkeypatch.setattr(answer_cache, "_corpus_state", lambda: ())
    monkeypatch.setattr(answer_cache, "_index_state", lambda: ())
    monkeypatch.setattr(answer_cache, "_profile_state", lambda *args: ())
    monkeypatch.setattr(answer_cache, "_pipeline_state", lambda: ())
    monkeypatch.setenv("GUARD_TERM_COVERAGE_MIN", "0")
    a = answer_cache.build_key("質問", top_k=3, max_context_chars=100)
    monkeypatch.setenv("GUARD_TERM_COVERAGE_MIN", "0.8")
    b = answer_cache.build_key("質問", top_k=3, max_context_chars=100)
    assert a != b and answer_cache._redis_key(a) != answer_cache._redis_key(b)


def test_g4_deterministic_coverage_uses_no_tokenizer():
    from rag_core import qa
    from rag_grounded import Chunk
    good = [Chunk(id="1", text="PR2 装置の仕様", source_doc="a", source_pages=(1,))]
    bad = [Chunk(id="2", text="別の資料", source_doc="b", source_pages=(1,))]
    assert qa._question_term_coverage("PR2 装置", good) == 1.0
    assert qa._question_term_coverage("PR2 装置", bad) == 0.0


def test_g5_rejects_embedded_qdrant_in_production(monkeypatch):
    from webapi import production_guard
    monkeypatch.setenv("VECTOR_BACKEND", "qdrant")
    monkeypatch.setenv("QDRANT_PATH", "/tmp/test-only-qdrant")
    assert any("QDRANT_PATH" in error for error in production_guard.production_problems())
    monkeypatch.delenv("QDRANT_PATH")
    assert not any("QDRANT_PATH" in error for error in production_guard.production_problems())


def test_g7_invalid_session_does_not_write_revocation(monkeypatch):
    from webapi import admin_auth, state_backend
    calls = []
    monkeypatch.setattr(state_backend, "revoke_session", lambda *args: calls.append(args))
    monkeypatch.setenv("ADMIN_AUTH_TOKEN", "test-only-token")
    cookie, _ = admin_auth.issue_admin_session()
    assert admin_auth.revoke_admin_session(cookie + "bad") is False
    assert calls == []


def test_g13_warmup_success_disabled_and_failure(monkeypatch, caplog):
    from rag_core import embedding_provider
    from webapi import main
    calls = []
    monkeypatch.setattr(embedding_provider, "is_local_provider", lambda: True)
    monkeypatch.setattr(embedding_provider, "embed_queries", lambda rows: calls.append(rows))
    monkeypatch.setenv("WARMUP_ON_START", "false")
    main._production_warmup()
    assert calls == []
    monkeypatch.setenv("WARMUP_ON_START", "true")
    main._production_warmup()
    assert calls == [["warmup"]]
    def fail(_rows):
        raise RuntimeError("synthetic load failure")
    monkeypatch.setattr(embedding_provider, "embed_queries", fail)
    main._production_warmup()
    assert "embedding warm-up failed" in caplog.text


def test_g8_sdk_retry_disabled_only_on_chat_clone():
    from types import SimpleNamespace
    from rag_core import qa
    class Client:
        def __init__(self):
            self.options = []
            self.chat = SimpleNamespace(completions=SimpleNamespace(create=lambda **kwargs: "response"))
        def with_options(self, **kwargs):
            self.options.append(kwargs)
            return self
    client = Client()
    assert qa._create_chat_completion(client, []) == "response"
    assert client.options == [{"max_retries": 0}]


@pytest.mark.parametrize("raw", ["不足", "不明", "不足: 根拠なし", "不明：対象不明"])
def test_g1_shortage_only_abstains_without_extractive_fallback(monkeypatch, raw):
    from rag_core import qa
    monkeypatch.setattr(qa, "extractive_fallback", lambda *args: pytest.fail("extractive fallback used"))
    result, fallback = qa._finalize_generated_answer(raw, "質問", [], "other")
    assert fallback is True and "根拠不足" in result


def test_g2_rejects_unquoted_assertion_next_to_cited_bullet():
    from rag_grounded import Chunk, validate_output
    chunks = [Chunk(id="1", text="営業開始は9時です。", source_doc="a", source_pages=(1,))]
    assert validate_output("- 営業開始は9時です [S1]\n不明: なし", chunks, "other")
    assert not validate_output("- 営業開始は9時です [S1]\n休日も営業します。", chunks, "other")


def test_g4_guard_disabled_by_default_and_enabled_threshold(monkeypatch):
    from rag_core import qa
    from rag_core.retrieval import RetrievedChunk
    from rag_grounded import Chunk
    monkeypatch.setattr(qa.config, "DISABLE_GUARD", False)
    monkeypatch.delenv("GUARD_TERM_COVERAGE_MIN", raising=False)
    monkeypatch.setattr(qa, "_guard_evidence", lambda _: {"best_vector_distance": None, "best_bm25_score": 1.0, "has_lexical_match": True})
    chunks = [Chunk(id="1", text="PR2", source_doc="a", source_pages=(1,))]
    retrieved = [RetrievedChunk(text="PR2", metadata={}, score=0.1)]
    assert qa.guard_merged_top("PR2 装置", "procedure", chunks, retrieved) is None
    monkeypatch.setenv("GUARD_TERM_COVERAGE_MIN", "0.8")
    assert qa.guard_merged_top("PR2 装置", "procedure", chunks, retrieved) == "low_term_coverage"


@pytest.mark.parametrize("backend", ["local", "redis"])
def test_g4_changed_guard_never_reuses_answer(monkeypatch, backend):
    from webapi import state_backend
    monkeypatch.setattr(answer_cache, "enabled", lambda: True)
    monkeypatch.setattr(answer_cache, "_corpus_state", lambda: ())
    monkeypatch.setattr(answer_cache, "_index_state", lambda: ())
    monkeypatch.setattr(answer_cache, "_profile_state", lambda *args: ())
    monkeypatch.setattr(answer_cache, "_pipeline_state", lambda: ())
    class FakeRedis:
        def __init__(self): self.data = {}
        def set(self, key, value, ex=None): self.data[key] = value
        def get(self, key): return self.data.get(key)
    fake = FakeRedis()
    monkeypatch.setattr(state_backend, "redis_client", lambda: fake)
    monkeypatch.setattr(state_backend, "redis_errors", lambda: (ConnectionError,))
    monkeypatch.setenv("ANSWER_CACHE_BACKEND", backend)
    monkeypatch.setenv("GUARD_TERM_COVERAGE_MIN", "0")
    before = answer_cache.build_key("架空質問", top_k=3, max_context_chars=100)
    answer_cache.put(before, {"answer_text": "old"})
    assert answer_cache.get(before)["answer_text"] == "old"
    monkeypatch.setenv("GUARD_TERM_COVERAGE_MIN", "0.8")
    after = answer_cache.build_key("架空質問", top_k=3, max_context_chars=100)
    assert answer_cache.get(after) is None


def test_g3_ambiguous_current_key_fails_closed(tmp_path):
    key = exact_match_key("同じ質問")
    path = tmp_path / "ambiguous.json"
    path.write_text(json.dumps({key: [{"metadata": {"id": "a", "question_text": "同じ質問", "tenant_id": "default"}}, {"metadata": {"id": "b", "question_text": "同じ質問", "tenant_id": "default"}}]}, ensure_ascii=False), encoding="utf-8")
    assert lookup.lookup_approved_qa_exact("同じ質問", tenant_id="default", index_path=path) == []


def test_g8_stream_sdk_retry_disabled_on_chat_clone():
    from types import SimpleNamespace
    from rag_core import qa
    class Client:
        def __init__(self):
            self.options = []
            chunk = SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content="ok"))])
            self.chat = SimpleNamespace(completions=SimpleNamespace(create=lambda **kwargs: iter([chunk])))
        def with_options(self, **kwargs):
            self.options.append(kwargs)
            return self
    client = Client()
    assert list(qa._stream_chat_completion(client, [])) == ["ok"]
    assert client.options == [{"max_retries": 0}]


def test_g9_verification_script_installs_static_checker():
    from pathlib import Path
    script = Path("tools/verify_all.sh").read_text(encoding="utf-8")
    assert "pytest pyflakes -c constraints/linux-py312.txt" in script
    assert '"$PY" -m pyflakes' in script


def test_g10_anthropic_pinned_in_both_hash_locked_sets():
    from pathlib import Path
    import re
    script = Path("scripts/lock_dependencies.sh").read_text(encoding="utf-8")
    assert "requirements-anthropic.txt" in script
    for constraints, lock in (("constraints/linux-py312.txt", "requirements.lock.txt"), ("constraints/linux-py312-embedding.txt", "requirements-embedding.lock.txt")):
        assert "anthropic==2.8.0" in Path(constraints).read_text(encoding="utf-8")
        text = Path(lock).read_text(encoding="utf-8")
        assert re.search(r"(?m)^anthropic==2\.8\.0\s*\\\n\s*--hash=sha256:[0-9a-f]{64}", text)


def test_g3_conflicting_answers_with_same_identifier_fail_closed(tmp_path):
    key = exact_match_key("同じ質問")
    path = tmp_path / "conflicting.json"
    path.write_text(json.dumps({key: [{"metadata": {"id": "same", "question_text": "同じ質問", "tenant_id": "default", "approved_answer": "A"}}, {"metadata": {"id": "same", "question_text": "同じ質問", "tenant_id": "default", "approved_answer": "B"}}]}, ensure_ascii=False), encoding="utf-8")
    assert lookup.lookup_approved_qa_exact("同じ質問", tenant_id="default", index_path=path) == []
