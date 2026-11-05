from __future__ import annotations

# Stage 1 regression tests: tenant leakage and authorization (items 1-1 .. 1-9).

import json
import os
import re
import threading
from pathlib import Path

import pytest
from fastapi import HTTPException

import config
from rag_core import approved_qa_exact_lookup as exact
from rag_core import approved_similar, profile_loader
from rag_core.product_profile import load_product_profile
from rag_core.product_route_policy import build_route_policy
from rag_core.tenant_profile import validate_tenant_profile_mapping
from webapi import admin_auth, ingestion_jobs, main, oidc_auth, rbac
from webapi.routers import chat as chat_router
from webapi.api_auth import ApiAuthContext, enforce_tenant_authorization, require_api_auth_headers


# --- 1-1 approved QA exact lookup ---------------------------------------------


def _write_exact_index(path: Path) -> Path:
    key = exact.normalize_exact_question("営業時間を教えてください")
    item_a = {
        "id": "approved_qa_pair:a",
        "metadata": {"id": "approved_qa_pair:a", "question_text": "営業時間を教えてください", "tenant_id": "tenant_a"},
    }
    item_default = {
        "id": "approved_qa_pair:d",
        "metadata": {"id": "approved_qa_pair:d", "question_text": "営業時間を教えてください"},
    }
    path.write_text(json.dumps({key: [item_a, item_default]}, ensure_ascii=False), encoding="utf-8")
    exact.clear_exact_index_cache()
    return path


def test_exact_lookup_filters_by_tenant(monkeypatch, tmp_path):
    index = _write_exact_index(tmp_path / "exact.json")
    monkeypatch.setenv(exact.EXACT_INDEX_PATH_ENV, str(index))

    hits_a = exact.lookup_approved_qa_exact("営業時間を教えてください", tenant_id="tenant_a")
    hits_b = exact.lookup_approved_qa_exact("営業時間を教えてください", tenant_id="tenant_b")
    hits_default = exact.lookup_approved_qa_exact("営業時間を教えてください", tenant_id="default")

    assert [h["id"] for h in hits_a] == ["approved_qa_pair:a"]
    assert hits_b == []
    assert [h["id"] for h in hits_default] == ["approved_qa_pair:d"]


def test_exact_lookup_requires_tenant_argument():
    with pytest.raises(TypeError):
        exact.lookup_approved_qa_exact("質問")  # type: ignore[call-arg]


def test_exact_lookup_is_disabled_without_configured_index(monkeypatch):
    monkeypatch.delenv(exact.EXACT_INDEX_PATH_ENV, raising=False)
    assert exact.lookup_approved_qa_exact("営業時間を教えてください", tenant_id="default") == []


def test_exact_lookup_refuses_eval_artifacts_in_runtime_config(monkeypatch, tmp_path):
    eval_dir = tmp_path / "fixed_qa_eval"
    eval_dir.mkdir()
    index = _write_exact_index(eval_dir / "exact.json")
    monkeypatch.setenv(exact.EXACT_INDEX_PATH_ENV, str(index))
    monkeypatch.delenv("APPROVED_QA_EXACT_ALLOW_EVAL_ARTIFACTS", raising=False)

    assert exact.configured_exact_index_path() is None
    assert exact.lookup_approved_qa_exact("営業時間を教えてください", tenant_id="default") == []


def test_exact_lookup_reloads_after_index_update(monkeypatch, tmp_path):
    index = _write_exact_index(tmp_path / "exact.json")
    monkeypatch.setenv(exact.EXACT_INDEX_PATH_ENV, str(index))
    assert exact.lookup_approved_qa_exact("営業時間を教えてください", tenant_id="tenant_a")

    index.write_text("{}", encoding="utf-8")
    os.utime(index, ns=(1, 1))

    assert exact.lookup_approved_qa_exact("営業時間を教えてください", tenant_id="tenant_a") == []


def test_chat_exact_lookup_not_called_when_approved_qa_disabled(monkeypatch):
    monkeypatch.setattr(config, "APPROVED_QA_ENABLED", False)
    monkeypatch.setattr(
        chat_router, "lookup_approved_qa_exact", lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not run"))
    )
    assert main._approved_exact_chat_lookup("質問", "tenant_a") is None


# --- 1-2 approved similar search ----------------------------------------------


def _qa_meta(qa_id: str, tenant_id: str | None) -> dict:
    meta = {"qa_id": qa_id, "question_text": "質問", "answer_text": "回答", "doc_type": "approved_qa_pair", "chunk_type": "qa_pair"}
    if tenant_id is not None:
        meta["tenant_id"] = tenant_id
    return meta


class _RecordingCollection:
    def __init__(self, metas):
        self.metas = metas
        self.calls = []

    def query(self, **kwargs):
        self.calls.append(kwargs)
        n = len(self.metas)
        return {"documents": [["Q: 質問\nA: 回答"] * n], "metadatas": [self.metas], "distances": [[0.1] * n]}


def test_similar_search_filters_tenant_in_where_and_results(monkeypatch):
    collection = _RecordingCollection([_qa_meta("qa-a", "tenant_a"), _qa_meta("qa-b", "tenant_b")])
    monkeypatch.setattr(approved_similar.embedder, "embed_queries", lambda queries, client=None: [[0.0, 1.0]])

    candidates = approved_similar.search_approved_similar_candidates(
        "質問", collection=collection, tenant_id="tenant_a", top_k=5
    )

    assert [c["qa_id"] for c in candidates] == ["qa-a"]
    clauses = collection.calls[0]["where"]["$and"]
    assert {"tenant_id": "tenant_a"} in clauses


def test_similar_search_does_not_retry_without_filter(monkeypatch):
    class _Failing:
        calls = 0

        def query(self, **kwargs):
            _Failing.calls += 1
            raise RuntimeError("filter failed")

    monkeypatch.setattr(approved_similar.embedder, "embed_queries", lambda queries, client=None: [[0.0]])

    with pytest.raises(RuntimeError):
        approved_similar.search_approved_similar_candidates("質問", collection=_Failing(), tenant_id="tenant_a")
    assert _Failing.calls == 1


def test_similar_search_keeps_embedding_client_separate_from_collection(monkeypatch):
    embedding_client = object()
    seen = {}
    collection = _RecordingCollection([_qa_meta("qa-d", None)])

    def _embed(queries, client=None):
        seen["client"] = client
        return [[0.0, 1.0]]

    monkeypatch.setattr(approved_similar.embedder, "embed_queries", _embed)
    monkeypatch.setattr(approved_similar.store, "get_vectorstore", lambda **kwargs: collection)

    candidates = approved_similar.search_approved_similar_candidates("質問", client=embedding_client, top_k=1)

    assert seen["client"] is embedding_client
    assert collection.calls and candidates[0]["qa_id"] == "qa-d"


# --- 1-3 admin auth and ingestion confinement -------------------------------------


def test_admin_routes_refused_when_admin_auth_not_configured(monkeypatch):
    for var in ("ADMIN_AUTH_ENABLED", "ADMIN_AUTH_TOKEN", "ADMIN_AUTH_ALLOW_INSECURE_DEV"):
        monkeypatch.delenv(var, raising=False)
    from fastapi.testclient import TestClient

    client = TestClient(main.app)
    assert client.get("/admin/review/items").status_code == 403
    assert client.get("/admin/ingestion/jobs").status_code == 403


@pytest.fixture
def ingest_env(monkeypatch, tmp_path):
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    monkeypatch.setenv("INGESTION_REGISTRY_PATH", str(tmp_path / "registry.sqlite3"))
    monkeypatch.setenv("INGEST_ALLOWED_ROOTS", str(inbox))
    monkeypatch.setattr(ingestion_jobs, "_vectorstore_has_collection", lambda name: False)
    ingestion_jobs.reset()
    return inbox


def test_ingest_input_outside_root_is_refused(ingest_env, tmp_path):
    outside = tmp_path / "outside.csv"
    outside.write_text("質問,回答\nA,B\n", encoding="utf-8")

    with pytest.raises(ValueError):
        ingestion_jobs.resolve_ingest_input(str(outside))
    with pytest.raises(ValueError):
        ingestion_jobs.resolve_ingest_input(str(ingest_env / ".." / "outside.csv"))


def test_ingest_symlink_input_is_refused(ingest_env, tmp_path):
    target = tmp_path / "secret.csv"
    target.write_text("質問,回答\nA,B\n", encoding="utf-8")
    link = ingest_env / "link.csv"
    try:
        link.symlink_to(target)
    except OSError:
        pytest.skip("symlinks not available")

    with pytest.raises(ValueError):
        ingestion_jobs.resolve_ingest_input(str(link))


def test_ingest_without_configured_roots_refuses_everything(monkeypatch, tmp_path):
    monkeypatch.delenv("INGEST_ALLOWED_ROOTS", raising=False)
    with pytest.raises(ValueError):
        ingestion_jobs.resolve_ingest_input(str(tmp_path / "a.csv"))


def test_ingest_input_count_is_bounded(ingest_env, monkeypatch):
    monkeypatch.setenv("INGEST_MAX_FILES", "2")
    with pytest.raises(ValueError):
        ingestion_jobs.run_dry_run([str(ingest_env / f"{i}.jsonl") for i in range(3)], collection="stage_v1")


# --- 1-4 RBAC ----------------------------------------------------------------------


def test_api_key_caller_counts_as_user_not_operator():
    ctx = ApiAuthContext(authenticated=True)
    rbac.enforce_role(ctx, rbac.ROLE_USER)
    with pytest.raises(HTTPException):
        rbac.enforce_role(ctx, rbac.ROLE_OPERATOR)
    assert rbac.caller_has_role(ctx, rbac.ROLE_OPERATOR) is False


def test_viewer_cannot_write_feedback_or_threads():
    viewer = ApiAuthContext(authenticated=True, role="viewer")
    with pytest.raises(HTTPException) as exc:
        rbac.enforce_role(viewer, rbac.ROLE_USER)
    assert exc.value.status_code == 403


def test_viewer_session_is_refused_on_admin_routes_even_with_token(monkeypatch):
    monkeypatch.setenv("ADMIN_AUTH_ENABLED", "true")
    monkeypatch.setenv("ADMIN_AUTH_TOKEN", "admin-secret")
    monkeypatch.setattr(
        oidc_auth, "resolve_oidc_session", lambda headers: ApiAuthContext(authenticated=True, role="viewer")
    )

    with pytest.raises(HTTPException) as exc:
        admin_auth.require_admin_auth_headers({"X-Admin-Token": "admin-secret"})
    assert exc.value.status_code == 403

    monkeypatch.setattr(
        oidc_auth, "resolve_oidc_session", lambda headers: ApiAuthContext(authenticated=True, role="operator")
    )
    admin_auth.require_admin_auth_headers({"X-Admin-Token": "admin-secret"})


# --- 1-5 product-preview profiles ----------------------------------------------------


@pytest.mark.parametrize("value", ["../../etc/passwd", "/etc/passwd", "configs/approved_similar_keyword_weights.json"])
def test_product_preview_profile_paths_are_rejected(value):
    with pytest.raises(HTTPException) as exc:
        main._product_preview_profile_path(value, main._PRODUCT_PREVIEW_KEYWORD_PROFILES, "keyword_profile")
    assert exc.value.status_code == 400


def test_product_preview_profile_names_resolve_inside_repo():
    path = main._product_preview_profile_path("balanced", main._PRODUCT_PREVIEW_THRESHOLD_PROFILES, "threshold_profile")
    assert Path(path).resolve().is_relative_to(Path(config.BASE_DIR).resolve())


def test_profile_override_is_request_scoped_and_leaves_environ(monkeypatch):
    monkeypatch.delenv("APPROVED_SIMILAR_KEYWORD_WEIGHTS", raising=False)
    observed = {}
    entered = threading.Event()
    release = threading.Event()

    def _request_a():
        with approved_similar.profile_overrides(keyword_profile_path="/profiles/a.json"):
            observed["a"] = approved_similar._configured_keyword_profile_path()
            entered.set()
            release.wait(5)

    def _request_b():
        entered.wait(5)
        observed["b"] = approved_similar._configured_keyword_profile_path()
        release.set()

    threads = [threading.Thread(target=_request_a), threading.Thread(target=_request_b)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(10)

    assert observed["a"] == "/profiles/a.json"
    assert observed["b"] is None
    assert "APPROVED_SIMILAR_KEYWORD_WEIGHTS" not in os.environ


def test_non_serving_profile_detection():
    assert main._is_non_serving_product_profile("dev_debug") is True
    assert main._is_non_serving_product_profile("production_safe") is False
    assert main._is_non_serving_product_profile(None) is False


# --- 1-6 rag_profile_id ------------------------------------------------------------


@pytest.mark.parametrize("value", ["../default", "/etc", "a/b", "..", "", "x" * 65])
def test_profile_loader_rejects_unsafe_ids(value):
    assert profile_loader.is_valid_rag_profile_id(value) is False
    if value:
        with pytest.raises(ValueError):
            profile_loader.load_rag_profile(value)


def test_profile_loader_warnings_do_not_contain_server_paths():
    profile_loader.reload_rag_profile_cache()
    profile = profile_loader.load_rag_profile("profile_that_does_not_exist")
    blob = json.dumps(profile, ensure_ascii=False)
    assert str(config.BASE_DIR) not in blob


def test_chat_rag_profile_id_validation_and_tenant_allowlist(monkeypatch):
    monkeypatch.delenv("RAG_PROFILE_TENANT_MAP", raising=False)
    with pytest.raises(HTTPException) as bad:
        main._resolve_rag_profile_id(main.ChatRequest(question="q", rag_profile_id="../x"), None, "tenant_a")
    assert bad.value.status_code == 400
    with pytest.raises(HTTPException) as forbidden:
        main._resolve_rag_profile_id(main.ChatRequest(question="q", rag_profile_id="custom"), None, "tenant_a")
    assert forbidden.value.status_code == 403

    monkeypatch.setenv("RAG_PROFILE_TENANT_MAP", "tenant_a=custom")
    assert main._resolve_rag_profile_id(main.ChatRequest(question="q", rag_profile_id="custom"), None, "tenant_a") == "custom"
    with pytest.raises(HTTPException):
        main._resolve_rag_profile_id(main.ChatRequest(question="q", rag_profile_id="custom"), None, "tenant_b")


# --- 1-7 safety overrides --------------------------------------------------------------


def test_overrides_cannot_disable_audit_or_review():
    policy = build_route_policy(
        load_product_profile("production_safe"),
        {"features": {"audit": False, "review_queue": False}, "answer_policy": {"fallback_to_no_answer": False}},
    )
    assert "audit" in policy["enabled_steps"]
    assert "review_queue" in policy["enabled_steps"]
    assert policy["answer_policy"]["fallback_to_no_answer"] is True
    assert "locked_override_ignored:features.audit" in policy["warnings"]


def test_production_rules_follow_operation_mode_not_only_name():
    profile = load_product_profile("dev_debug")
    profile["profile_name"] = "customer_copy"
    profile["operation_mode"] = "production_safe"
    policy = build_route_policy(profile)
    assert "debug_comparison" not in policy["enabled_steps"]
    assert "feedback_preview_rerank" not in policy["enabled_steps"]


def test_runtime_tenants_cannot_allow_dev_or_evaluation_profiles():
    mapping = {
        "mapping_version": "1",
        "default_profile": "production_safe",
        "unknown_tenant_policy": "default_profile",
        "tenants": {
            "tenant_a": {
                "tenant_id": "tenant_a",
                "customer_id": "customer_a",
                "profile": "production_safe",
                "allowed_profiles": ["production_safe", "dev_debug"],
                "status": "active",
            }
        },
    }
    errors = validate_tenant_profile_mapping(mapping)
    assert any("must not include dev_debug or evaluation" in e for e in errors)


# --- 1-8 tenant map fail-closed ----------------------------------------------------------


def test_api_key_without_tenant_map_reaches_only_default(monkeypatch):
    for var in ("API_AUTH_TENANT_MAP", "API_AUTH_ALLOW_ANY_TENANT_WITHOUT_MAP", "ENTERPRISE_AUTH_ENABLED", "ENTERPRISE_OIDC_ENABLED"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("API_AUTH_ENABLED", "true")
    monkeypatch.setenv("API_AUTH_KEYS", "key-a")

    ctx = require_api_auth_headers({"X-Api-Key": "key-a"})

    enforce_tenant_authorization(ctx, "default")
    with pytest.raises(HTTPException):
        enforce_tenant_authorization(ctx, "tenant_b")


def test_oidc_session_without_tenant_map_reaches_only_default(monkeypatch):
    monkeypatch.delenv("OIDC_ALLOW_ANY_TENANT_WITHOUT_MAP", raising=False)
    monkeypatch.delenv("ENTERPRISE_AUTH_ENABLED", raising=False)
    monkeypatch.setattr(
        oidc_auth,
        "resolve_oidc_session",
        lambda headers: ApiAuthContext(authenticated=True, tenant_authorization_enabled=False, key_fingerprint="fp"),
    )

    ctx = require_api_auth_headers({})

    assert ctx.tenant_authorization_enabled is True
    assert ctx.allowed_tenants == frozenset({"default"})


# --- 1-9 persisted job and collection registry --------------------------------------------


def _csv(inbox: Path) -> Path:
    path = inbox / "faq.csv"
    path.write_text("質問,回答\n営業時間は？,9時から17時です\n", encoding="utf-8")
    return path


def _fake_ingest(chunks, collection):
    return {"ingested": len(chunks), "skipped": 0, "embedding_fingerprint": {}}


def test_registry_survives_restart_and_isolates_tenants(ingest_env, monkeypatch):
    monkeypatch.setattr(ingestion_jobs, "_ingest_chunks", _fake_ingest)
    job = ingestion_jobs.run_raw_document_job(
        [str(_csv(ingest_env))], expected_tenant="tenant_a", collection="tenant_a_stage_v1", execute=True
    )
    assert job["ok"] is True
    assert re.fullmatch(r"job-[0-9a-f]{32}", job["job_id"])

    # A restart keeps no in-process state: everything is read back from SQLite.
    assert ingestion_jobs.get_job(job["job_id"])["collection"] == "tenant_a_stage_v1"
    assert ingestion_jobs.staging_collection_status("tenant_a_stage_v1", tenant_id="tenant_a")["allowed"] is True
    assert (
        ingestion_jobs.staging_collection_status("tenant_a_stage_v1", tenant_id="tenant_b")["reason"]
        == "staging_collection_forbidden_for_tenant"
    )
    with pytest.raises(PermissionError):
        ingestion_jobs.run_raw_document_job(
            [str(_csv(ingest_env))], expected_tenant="tenant_b", collection="tenant_a_stage_v1", execute=True
        )


def test_unknown_collection_is_not_queryable(ingest_env):
    assert ingestion_jobs.staging_collection_status("never_registered", tenant_id="tenant_a") == {
        "allowed": False,
        "reason": "staging_collection_unknown",
    }


def test_existing_unregistered_vectorstore_collection_cannot_be_claimed(ingest_env, monkeypatch):
    monkeypatch.setattr(ingestion_jobs, "_ingest_chunks", _fake_ingest)
    monkeypatch.setattr(ingestion_jobs, "_vectorstore_has_collection", lambda name: True)

    with pytest.raises(PermissionError):
        ingestion_jobs.run_raw_document_job(
            [str(_csv(ingest_env))], expected_tenant="tenant_a", collection="legacy_stage", execute=True
        )


def test_dry_run_does_not_register_collection(ingest_env, monkeypatch):
    monkeypatch.setattr(ingestion_jobs, "_ingest_chunks", _fake_ingest)
    ingestion_jobs.run_raw_document_job(
        [str(_csv(ingest_env))], expected_tenant="tenant_a", collection="dry_stage", execute=False
    )
    assert ingestion_jobs.get_collection("dry_stage") is None
