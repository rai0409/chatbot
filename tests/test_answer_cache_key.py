import json
import os

import pytest

import config
from rag_core import answer_cache, index_generation, profile_loader, query_preprocess
from schemas import AnswerResult
from webapi import main
from webapi.routers import chat as chat_router


@pytest.fixture
def state(monkeypatch, tmp_path):
    chunks = tmp_path / "chunks.jsonl"
    chunks.write_text('{"id":"c1","text":"本文"}\n', encoding="utf-8")
    monkeypatch.setattr(config, "CHUNKS_JSONL_PATH", str(chunks))
    monkeypatch.setattr(config, "APPROVED_QA_ENABLED", False)
    monkeypatch.setattr(config, "ANSWER_CACHE_ENABLED", True)
    monkeypatch.setattr(config, "INDEX_DIR", str(tmp_path / "index"))
    monkeypatch.setattr(config, "RUNS_DIR", str(tmp_path / "runs"))
    monkeypatch.setattr(profile_loader, "PROFILE_ROOT", tmp_path / "profiles")
    monkeypatch.setattr(query_preprocess, "RULES_DIR", tmp_path / "rules")
    for name in ("CHROMA_COLLECTION", "ACTIVE_GENERATION_FILE", "VECTOR_BACKEND", "COMPOSE_QUERY_MODE"):
        monkeypatch.delenv(name, raising=False)
    answer_cache.clear()
    return tmp_path


def _key(tenant="default", profile="default"):
    return answer_cache.build_key("質問です", top_k=5, max_context_chars=8000, tenant_id=tenant, rag_profile_id=profile)


def _write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    stat = os.stat(path)
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000))


def test_identical_state_gives_the_same_key(state):
    assert _key() == _key()


def test_profile_files_are_part_of_the_key(state):
    before = _key()
    synonyms = state / "profiles" / "default" / "exported" / "synonyms.json"
    _write(synonyms, json.dumps({"synonyms": []}))
    created = _key()
    _write(synonyms, json.dumps({"synonyms": [{"canonical": "a", "synonyms": ["b"], "enabled": True}]}))
    edited = _key()
    assert len({before, created, edited}) == 3


def test_the_profile_id_is_part_of_the_key(state):
    assert _key(profile="default") != _key(profile="acme_profile")


def test_query_rules_change_the_key_of_their_tenant_only(state):
    acme_before, other_before = _key(tenant="acme"), _key(tenant="other")
    _write(state / "rules" / "tenants" / "acme.json", json.dumps({"strip_prefix": ["^あの"]}))
    assert _key(tenant="acme") != acme_before
    assert _key(tenant="other") == other_before
    _write(state / "rules" / "colloquial_ja.json", json.dumps({"strip_prefix": []}))
    assert _key(tenant="other") != other_before


def test_collection_backend_and_generation_are_part_of_the_key(state, monkeypatch):
    base = _key()
    monkeypatch.setenv("CHROMA_COLLECTION", "another_collection")
    assert _key() != base
    monkeypatch.delenv("CHROMA_COLLECTION")
    monkeypatch.setenv("VECTOR_BACKEND", "qdrant")
    assert _key() != base
    monkeypatch.delenv("VECTOR_BACKEND")
    corpus = str(config.CHUNKS_JSONL_PATH)

    def activate(generation_id):
        entry = {"collection": "gen_a", "keyword_index_path": corpus, "generation_id": generation_id}
        index_generation._write_pointer({"schema_version": 1, "active": entry, "history": []})

    activate("gid-1")
    first = _key()
    activate("gid-2")
    assert _key() != first


@pytest.mark.parametrize(
    "name, value",
    [
        ("HYBRID_FUSION", "dbsf"),
        ("HYBRID_DENSE_WEIGHT", 2.0),
        ("KEYWORD_BOOST_MAX_DELTA", 0.2),
        ("RAG_SOFT_DIST_OTHER", 0.5),
        ("MAX_PARENT_CONTEXT_CHARS", 100),
        ("CHAT_COMPLETION_MAX_TOKENS", 7),
        ("OPENAI_BASE_URL", "http://llm.local/v1"),
    ],
)
def test_retrieval_guard_and_llm_settings_are_part_of_the_key(state, monkeypatch, name, value):
    base = _key()
    monkeypatch.setattr(config, name, value, raising=False)
    assert _key() != base


def test_environment_switches_read_by_the_pipeline_are_part_of_the_key(state, monkeypatch):
    base = _key()
    monkeypatch.setenv("COMPOSE_QUERY_MODE", "legacy")
    assert _key() != base


def test_unrelated_environment_does_not_change_the_key(state, monkeypatch):
    base = _key()
    monkeypatch.setenv("SOME_UNRELATED_VARIABLE", "x")
    assert _key() == base


def _fake_pipeline(calls):
    def fake(question, client=None, top_k=20, max_context_chars=8000, intent_override=None, **kwargs):
        calls.append(question)
        answer = AnswerResult(
            answer_text=f"回答本文 {len(calls)}",
            answer_with_footnotes=f"回答本文 {len(calls)}\n参考資料:\n- [1] doc.pdf p1",
            intent="other",
            guard_reason=None,
            used_fallback=False,
            citations=[],
            retrieved=[],
            rewritten_query="",
            augmented_query="",
        )
        trace = {
            "request_id": f"req-{len(calls)}",
            "normalized_query": question,
            "intent": "other",
            "final_guard_reason": None,
            "final_used_fallback": False,
            "citations_count": 0,
            "latency_ms": 1,
            "after_rerank": [],
        }
        return answer, trace

    return fake


def test_a_changed_profile_or_setting_is_not_answered_from_the_cache(state, monkeypatch):
    calls = []
    monkeypatch.setattr(chat_router, "ensure_openai_client", lambda base_url=None: object())
    monkeypatch.setattr(chat_router, "answer_query_with_trace", _fake_pipeline(calls))

    main.chat(main.ChatRequest(question="質問です"))
    main.chat(main.ChatRequest(question="質問です"))
    assert len(calls) == 1  # the second call is a cache hit

    _write(state / "profiles" / "default" / "exported" / "synonyms.json", json.dumps({"synonyms": []}))
    main.chat(main.ChatRequest(question="質問です"))
    assert len(calls) == 2  # an edited profile file: the pipeline runs again

    monkeypatch.setattr(config, "HYBRID_FUSION", "dbsf")
    main.chat(main.ChatRequest(question="質問です"))
    assert len(calls) == 3  # a changed retrieval setting: the pipeline runs again
    main.chat(main.ChatRequest(question="質問です"))
    assert len(calls) == 3  # and the new state is cached again
