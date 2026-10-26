from __future__ import annotations

# Stage 8 regression tests: .env.example, staging hybrid, conversation
# completion, reading lane / morphology intent, synonym review, async
# ingestion, shared-state backends, OIDC revocation, Qdrant migration,
# CI regression gate, staged retrieval.

import json
import re
from pathlib import Path

import pytest

import config
from rag_core import answer_cache, conversation_context, ja_terms, qa
from rag_core.retrieval import RetrievedChunk
from webapi import ingestion_jobs, oidc_auth, state_backend, synonym_review
from webapi.rate_limit import RedisRateLimiter


# --- 1. .env.example ---------------------------------------------------------------------


def test_env_example_lists_new_settings_once():
    text = (Path(config.BASE_DIR) / ".env.example").read_text(encoding="utf-8")
    keys = re.findall(r"^([A-Z][A-Z0-9_]+)=", text, flags=re.MULTILINE)
    for required in (
        "APP_ENV", "METRICS_TOKEN", "INGEST_ALLOWED_ROOTS", "NORMALIZATION_PROFILE", "VECTOR_BACKEND",
        "QUERY_PREPROCESS_ENABLED", "READING_LANE_ENABLED", "STAGED_RETRIEVAL_MARGIN", "REDIS_URL",
        "POSTGRES_URL", "CONVERSATION_CONTEXT_ENABLED", "AUDIT_QUESTION_MODE",
    ):
        assert required in keys
    assert len(keys) == len(set(keys))


# --- 2. staging collections ---------------------------------------------------------------


def _recorder(calls, name):
    def _fn(query, *args, **kwargs):
        calls.append((name, kwargs.get("collection_name")))
        return [RetrievedChunk(text="本文", metadata={"id": f"{name}-1", "rrf_score": 0.03}, score=0.1)]

    return _fn


def _run_retrieval(monkeypatch, collection):
    calls = []
    monkeypatch.setattr(qa, "hybrid_retrieve", _recorder(calls, "hybrid"))
    monkeypatch.setattr(qa, "vector_retrieve", _recorder(calls, "vector"))
    qa._retrieve_and_rerank(
        "質問", "質問", scoring_query="質問", client=object(), top_k=3, intent="other",
        query_type="other", collection_name=collection,
    )
    return calls


def test_staging_with_keyword_corpus_uses_hybrid(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "INDEX_DIR", str(tmp_path / "index"))
    corpus = tmp_path / "index" / "collections" / "stage_v1.jsonl"
    corpus.parent.mkdir(parents=True)
    corpus.write_text('{"id":"c1","text":"本文"}\n', encoding="utf-8")
    assert _run_retrieval(monkeypatch, "stage_v1") == [("hybrid", "stage_v1")]
    assert _run_retrieval(monkeypatch, "stage_without_corpus") == [("vector", "stage_without_corpus")]


# --- 3. conversation completion ---------------------------------------------------------------


def test_follow_up_question_gets_previous_subject():
    result = conversation_context.complete_question("期限は？", ["有給休暇の申請方法を教えて"])
    assert result["completed"] is True
    assert result["question"] == "有給休暇 申請方法 期限は？"
    full = conversation_context.complete_question("退職金の支給時期はいつですか", ["有給休暇の申請方法を教えて"])
    assert full["completed"] is False


def test_demonstrative_follow_up():
    result = conversation_context.complete_question("それのパートの場合は？", ["育児休業の取得条件"])
    assert result["completed"] is True
    assert result["question"] == "育児休業 取得条件 パートの場合は？"


def test_chat_uses_completed_question_only_with_thread_and_flag(monkeypatch):
    from webapi import conversation_store, main

    monkeypatch.setenv("CONVERSATION_CONTEXT_ENABLED", "true")
    monkeypatch.setenv("CONVERSATION_HISTORY_ENABLED", "true")
    monkeypatch.setattr(conversation_store, "get_thread", lambda t, i, th: [{"question": "有給休暇の申請方法を教えて"}])
    req = main.ChatRequest(question="期限は？", thread_id="t1")
    question, trace = main._conversation_question(req, None, "default")
    assert question == "有給休暇 申請方法 期限は？" and trace["original_question"] == "期限は？"
    assert main._conversation_question(main.ChatRequest(question="期限は？"), None, "default")[0] == "期限は？"


# --- 4. reading lane / morphology intent (Sudachi) ------------------------------------------------


def test_reading_tokens_join_homophones():
    pytest.importorskip("sudachipy")
    if not ja_terms.morph_available():
        pytest.skip("Sudachi dictionary not installed")
    assert set(ja_terms.reading_tokens("保険")) & set(ja_terms.reading_tokens("保健"))


def test_procedure_form_by_morphology():
    pytest.importorskip("sudachipy")
    if not ja_terms.morph_available():
        pytest.skip("Sudachi dictionary not installed")
    assert ja_terms.has_procedure_form("パスワードを変えたい") is True
    assert ja_terms.has_procedure_form("規程の変更点") is False


# --- 5. synonym review ---------------------------------------------------------------------------


def test_synonym_review_decide_and_apply(monkeypatch, tmp_path):
    from rag_core import profile_loader

    monkeypatch.setattr(config, "RUNS_DIR", str(tmp_path / "runs"))
    monkeypatch.setattr(profile_loader, "PROFILE_ROOT", tmp_path / "profiles")
    path = synonym_review.candidates_path()
    path.parent.mkdir(parents=True)
    rows = [
        {"candidate_id": "syn_a", "status": "candidate", "method": "normalized_form", "canonical": "サーバー", "synonyms": ["サーバ"]},
        {"candidate_id": "syn_b", "status": "candidate", "method": "embedding", "canonical": "退職", "synonyms": ["離職"]},
    ]
    path.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n", encoding="utf-8")

    synonym_review.decide("syn_a", "approved")
    synonym_review.decide("syn_b", "rejected")
    with pytest.raises(ValueError):
        synonym_review.decide("syn_a", "maybe")
    assert synonym_review.list_candidates()["counts"] == {"approved": 1, "rejected": 1}

    result = synonym_review.apply_to_profile("tenant_a")
    saved = json.loads((tmp_path / "profiles" / "tenant_a" / "exported" / "synonyms.json").read_text(encoding="utf-8"))
    assert result["approved_rows"] == 1
    assert [e["canonical"] for e in saved["synonyms"]] == ["サーバー"]
    with pytest.raises(ValueError):
        synonym_review.apply_to_profile("../x")


# --- 6. asynchronous ingestion -------------------------------------------------------------------


class _InlineExecutor:
    def submit(self, fn):
        fn()


def test_async_job_is_queued_then_completed(monkeypatch, tmp_path):
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    (inbox / "faq.csv").write_text("質問,回答\n営業時間は？,9時から17時です\n", encoding="utf-8")
    monkeypatch.setenv("INGESTION_REGISTRY_PATH", str(tmp_path / "registry.sqlite3"))
    monkeypatch.setenv("INGEST_ALLOWED_ROOTS", str(inbox))
    monkeypatch.setattr(ingestion_jobs, "_executor", lambda: _InlineExecutor())
    ingestion_jobs.reset()

    queued = ingestion_jobs.submit_raw_document_job(
        [str(inbox / "faq.csv")], expected_tenant="tenant_a", collection="stage_async", execute=False
    )

    assert queued["status"] == "queued" and queued["async"] is True
    done = ingestion_jobs.get_job(queued["job_id"])
    assert done["status"] == "ok" and done["chunks_generated"] == 1
    with pytest.raises(ValueError):
        ingestion_jobs.submit_raw_document_job([str(tmp_path / "outside.csv")], expected_tenant="t", collection="s")


# --- 7. shared state ----------------------------------------------------------------------------


class _FakeRedis:
    def __init__(self):
        self.data = {}

    def pipeline(self):
        outer = self

        class _Pipe:
            def __init__(self):
                self.ops = []

            def incr(self, key):
                self.ops.append(("incr", key))

            def expire(self, key, ttl):
                self.ops.append(("expire", key))

            def execute(self):
                out = []
                for op, key in self.ops:
                    if op == "incr":
                        outer.data[key] = int(outer.data.get(key, 0)) + 1
                        out.append(outer.data[key])
                    else:
                        out.append(True)
                return out

        return _Pipe()

    def get(self, key):
        return self.data.get(key)

    def set(self, key, value, ex=None):
        self.data[key] = value

    def exists(self, key):
        return key in self.data


def test_redis_rate_limit_and_answer_cache(monkeypatch):
    fake = _FakeRedis()
    monkeypatch.setattr(state_backend, "redis_client", lambda: fake)
    limiter = RedisRateLimiter(clock=lambda: 0.0)
    assert limiter.check("fp", 1)[0] is True
    assert limiter.check("fp", 1)[0] is False

    monkeypatch.setenv("ANSWER_CACHE_BACKEND", "redis")
    monkeypatch.setattr(config, "ANSWER_CACHE_ENABLED", True)
    answer_cache.put(("k",), {"answer_text": "回答"})
    assert answer_cache.get(("k",)) == {"answer_text": "回答"}


def test_postgres_adapter_translates_placeholders():
    seen = {}

    class _Conn:
        def execute(self, sql, params):
            seen["sql"], seen["params"] = sql, params

    ingestion_jobs._PgConn(_Conn()).execute("SELECT 1 FROM jobs WHERE job_id = ?", ("j",))
    assert seen == {"sql": "SELECT 1 FROM jobs WHERE job_id = %s", "params": ("j",)}


# --- 8 / 13. OIDC session revocation ---------------------------------------------------------------

_SECRET = "s" * 40


def test_logout_revokes_the_session(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "RUNS_DIR", str(tmp_path / "runs"))
    monkeypatch.setenv("ENTERPRISE_OIDC_ENABLED", "true")
    monkeypatch.setenv("OIDC_SESSION_SECRET", _SECRET)
    monkeypatch.delenv("SESSION_REVOCATION_BACKEND", raising=False)
    token = oidc_auth._sign({"fp": "fp1", "authz": True, "tenants": "default", "sid": "sid-1", "iat": 100}, _SECRET, 3600)
    headers = {"cookie": f"{oidc_auth.SESSION_COOKIE}={token}"}

    assert oidc_auth.resolve_oidc_session(headers) is not None
    assert oidc_auth.revoke_session_from_headers(headers) is True
    assert oidc_auth.resolve_oidc_session(headers) is None


def test_admin_revocation_of_an_identity(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "RUNS_DIR", str(tmp_path / "runs"))
    monkeypatch.setenv("ENTERPRISE_OIDC_ENABLED", "true")
    monkeypatch.setenv("OIDC_SESSION_SECRET", _SECRET)
    import time

    old = oidc_auth._sign({"fp": "fp2", "authz": True, "tenants": "default", "sid": "a", "iat": int(time.time()) - 10}, _SECRET, 3600)
    oidc_auth.revoke_identity("fp2")
    assert oidc_auth.resolve_oidc_session({"cookie": f"{oidc_auth.SESSION_COOKIE}={old}"}) is None


# --- 10. Qdrant embeddings and migration ---------------------------------------------------------------


def test_migration_copies_vectors_without_reembedding(monkeypatch, tmp_path):
    pytest.importorskip("qdrant_client")
    chromadb = pytest.importorskip("chromadb")
    from rag_core import vector_backend
    from scripts.migrate_chroma_to_qdrant import migrate

    monkeypatch.setattr(config, "VECTORSTORE_DIR", str(tmp_path / "vs"))
    monkeypatch.setenv("QDRANT_PATH", str(tmp_path / "qdrant"))
    vector_backend._CLIENT.clear()
    source = chromadb.PersistentClient(path=str(tmp_path / "vs")).get_or_create_collection(
        "mig", metadata={"hnsw:space": "cosine", "embed_model": "m"}
    )
    source.add(ids=["a", "b"], documents=["一", "二"], embeddings=[[1.0, 0.0], [0.0, 1.0]], metadatas=[{"tenant_id": "default"}] * 2)

    report = migrate("mig")

    assert report["ok"] is True and report["target_count"] == 2
    got = vector_backend.QdrantCollection("mig").get(include=["embeddings"], limit=1)
    assert len(got["embeddings"][0]) == 2
    assert vector_backend.QdrantCollection("mig").metadata.get("embed_model") == "m"


# --- 11. CI regression gate ------------------------------------------------------------------------------


def test_regression_gate_compares_against_baseline():
    from scripts.eval_regression_gate import compare

    baseline = {"bm25_only": {"mean_mrr_at_k": 0.8, "mean_ndcg_at_k": 0.7, "gold_doc_hits": 10, "abstain_passes": 5}}
    same = {"bm25_only": {"mean_mrr_at_k": 0.79, "mean_ndcg_at_k": 0.7, "gold_doc_hits": 10, "abstain_passes": 5}}
    worse = {"bm25_only": {"mean_mrr_at_k": 0.7, "mean_ndcg_at_k": 0.7, "gold_doc_hits": 9, "abstain_passes": 5}}
    assert compare(baseline, same, 0.02) == []
    assert len(compare(baseline, worse, 0.02)) == 2


# --- 12. staged retrieval --------------------------------------------------------------------------------


def test_confident_first_pass_skips_the_augmented_pass(monkeypatch):
    calls = []

    def _hybrid(query, *args, **kwargs):
        calls.append(query)
        return [
            RetrievedChunk(text="一", metadata={"id": "a", "rrf_score": 0.05}, score=0.1),
            RetrievedChunk(text="二", metadata={"id": "b", "rrf_score": 0.01}, score=0.2),
        ]

    monkeypatch.setattr(qa, "hybrid_retrieve", _hybrid)
    monkeypatch.setenv("STAGED_RETRIEVAL_MARGIN", "0.02")
    stats = {}
    qa._retrieve_and_rerank(
        "退職", "退職 定義 意味", scoring_query="退職", client=object(), top_k=3, intent="other",
        query_type="other", stats=stats,
    )
    assert calls == ["退職"] and stats == {"skipped_augmented_pass": True}

    calls.clear()
    monkeypatch.setenv("STAGED_RETRIEVAL_MARGIN", "0")
    qa._retrieve_and_rerank(
        "退職", "退職 定義 意味", scoring_query="退職", client=object(), top_k=3, intent="other", query_type="other"
    )
    assert calls == ["退職", "退職 定義 意味"]
