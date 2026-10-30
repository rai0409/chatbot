"""Stage 15: scanned PDFs through the web ingestion path, collection names,
document deletion, staging -> served promotion, tenant-scoped administrators
and the audit of admin actions. Synthetic data only.
"""
from __future__ import annotations

import hashlib
import json
from types import SimpleNamespace

import pytest

import config

ADMIN_TOKEN = "installation-admin-token-not-a-secret-0123"
TENANT_A_TOKEN = "tenant-a-admin-token-not-a-secret-0123456"


# --- 1. scanned PDFs ------------------------------------------------------------------------------


def _scanned_pdf(path):
    from rag_core import extraction

    extraction.load()
    from docextract.testing import simple_pdf

    # A page with drawings only: no text layer, like a scan.
    path.write_bytes(simple_pdf([{"rects": [(72, 72, 300, 200)], "lines": [(72, 250, 500, 250)]}]))
    return path


def test_scanned_pdf_without_ocr_says_why(tmp_path, monkeypatch):
    pytest.importorskip("pdfplumber")
    from rag_core.document_converters import pdf_adapter

    monkeypatch.setenv("INGEST_PDF_OCR", "off")
    with pytest.raises(pdf_adapter.OcrRequired):
        pdf_adapter.convert_pdf(_scanned_pdf(tmp_path / "scan.pdf"))


def test_scanned_pdf_is_ocred_in_the_web_path(tmp_path, monkeypatch):
    pytest.importorskip("pdfplumber")
    from rag_core.document_converters import pdf_adapter
    from scripts import pdf_to_canonical_jsonl

    monkeypatch.setenv("INGEST_PDF_OCR", "on")
    monkeypatch.setattr(pdf_to_canonical_jsonl, "_ensure_ocr_available", lambda: None)
    monkeypatch.setattr(pdf_to_canonical_jsonl, "_ocr_page_result",
                        lambda page, dpi, lang: SimpleNamespace(text="経費精算の締め日は毎月25日とする。", confidence=95.0))
    rows = pdf_adapter.convert_pdf(_scanned_pdf(tmp_path / "0123456789ab_scan.pdf"), source_doc="経費規程.pdf")
    assert rows and "締め日" in rows[0]["text"]
    assert all(r["source_doc"] == "経費規程.pdf" for r in rows)
    assert all("0123456789ab" not in str(r["id"]) for r in rows)  # ids from the document name


def test_ocr_mode_setting(monkeypatch):
    from rag_core.document_converters import pdf_adapter

    monkeypatch.setenv("INGEST_PDF_OCR", "off")
    assert pdf_adapter.ocr_mode() is False
    monkeypatch.setenv("INGEST_PDF_OCR", "on")
    assert pdf_adapter.ocr_mode() is True


# --- 4. collection names --------------------------------------------------------------------------


def test_collection_names_are_validated_and_paths_unique(tmp_path, monkeypatch):
    from rag_core import retrieval
    from webapi import ingestion_jobs

    assert ingestion_jobs.validate_collection_name("pilot_staging_v1") == "pilot_staging_v1"
    for bad in ("a b", "../x", "a..b", "", "-a"):
        with pytest.raises(ValueError):
            ingestion_jobs.validate_collection_name(bad)
    monkeypatch.setenv("INGEST_ALLOWED_ROOTS", str(tmp_path))
    with pytest.raises(ValueError, match="invalid collection name"):
        ingestion_jobs.run_raw_document_job([str(tmp_path / "x.csv")], expected_tenant="default", collection="a b")
    assert retrieval.keyword_index_path_for_collection("a b") != retrieval.keyword_index_path_for_collection("a_b")


# --- 2 / 3. deletion and promotion ----------------------------------------------------------------


@pytest.fixture
def stores(tmp_path, monkeypatch):
    pytest.importorskip("chromadb")
    from rag_core import embedder, retrieval, store
    from webapi import ingestion_jobs

    monkeypatch.setattr(config, "VECTORSTORE_DIR", str(tmp_path / "vectorstore"))
    monkeypatch.setattr(config, "VECTORSTORE_COLLECTION_NAME", "served_test")
    monkeypatch.setattr(config, "CHUNKS_JSONL_PATH", str(tmp_path / "served.jsonl"))
    monkeypatch.setattr(config, "INDEX_DIR", str(tmp_path / "index"))
    monkeypatch.delenv("CHROMA_COLLECTION", raising=False)
    monkeypatch.setenv("VECTOR_BACKEND", "chroma")
    monkeypatch.setenv("INGESTION_REGISTRY_PATH", str(tmp_path / "registry.sqlite3"))

    def vectors(texts, client=None, provider_name=None):
        out = []
        for text in texts:
            digest = hashlib.sha256(str(text).encode("utf-8")).digest()
            out.append([b / 255.0 for b in digest[:16]])
        return out

    monkeypatch.setattr(embedder, "embed_documents", vectors)
    monkeypatch.setattr(embedder, "embed_queries", vectors)
    store.reset_vectorstore_clients()
    retrieval._INDEX_CACHE.update({"path": None, "mtime": None, "index": None})
    yield ingestion_jobs
    store.reset_vectorstore_clients()


def _rows(tenant, doc, n, prefix=""):
    return [{"id": f"{tenant}:{doc}:{i}", "text": f"{prefix}{doc} の本文 {i}", "source_doc": doc, "tenant_id": tenant,
             "type": "pdf", "quality": "high", "searchable": 1} for i in range(n)]


def _served_ids(doc=None):
    from rag_core import store

    where = {"source_doc": doc} if doc else None
    return sorted(store.get_vectorstore(create_if_missing=False).get(where=where, include=[])["ids"])


def _served_keyword_docs():
    path = config.CHUNKS_JSONL_PATH
    try:
        lines = open(path, encoding="utf-8").read().splitlines()
    except FileNotFoundError:
        return []
    return sorted({json.loads(line)["source_doc"] for line in lines if line.strip()})


def test_promote_then_delete(stores):
    from webapi import collection_admin

    ingestion_jobs = stores
    from rag_core import store

    store.get_vectorstore()  # the served collection exists (empty)
    ingestion_jobs.register_collection("pilot_staging_v1", owner_tenant="tenant_a", status=ingestion_jobs.COLLECTION_READY)
    ingestion_jobs._ingest_chunks(_rows("tenant_a", "規程.pdf", 3) + _rows("tenant_a", "手順書.pdf", 2), "pilot_staging_v1")

    plan = collection_admin.promote_staging("pilot_staging_v1", tenant="tenant_a")
    assert (plan["executed"], plan["documents"], plan["chunks"]) == (False, 2, 5)
    assert _served_ids() == []
    done = collection_admin.promote_staging("pilot_staging_v1", tenant="tenant_a", execute=True)
    assert done["executed"] and len(_served_ids()) == 5 and done["keyword_rows_written"] == 5
    assert _served_keyword_docs() == ["手順書.pdf", "規程.pdf"]

    # The document shrinks in staging: promoting again removes the stale chunks.
    ingestion_jobs._ingest_chunks(_rows("tenant_a", "規程.pdf", 1, "改訂 "), "pilot_staging_v1")
    again = collection_admin.promote_staging("pilot_staging_v1", tenant="tenant_a", execute=True)
    assert again["served_chunks_deleted"] == 2 and _served_ids("規程.pdf") == ["tenant_a:規程.pdf:0"]

    dry = collection_admin.delete_documents(None, tenant="tenant_a", source_docs=["規程.pdf"])
    assert dry["documents"] == {"規程.pdf": 1} and _served_ids("規程.pdf")
    collection_admin.delete_documents(None, tenant="tenant_a", source_docs=["規程.pdf"], execute=True)
    assert _served_ids("規程.pdf") == [] and len(_served_ids("手順書.pdf")) == 2
    assert _served_keyword_docs() == ["手順書.pdf"]


def test_promotion_and_deletion_check_the_owner(stores):
    from webapi import collection_admin

    ingestion_jobs = stores
    ingestion_jobs.register_collection("pilot_staging_v2", owner_tenant="tenant_a")
    with pytest.raises(PermissionError):
        collection_admin.promote_staging("pilot_staging_v2", tenant="tenant_b")
    with pytest.raises(ValueError, match="not ready"):
        collection_admin.promote_staging("pilot_staging_v2", tenant="tenant_a")
    with pytest.raises(PermissionError):
        collection_admin.delete_documents("pilot_staging_v2", tenant="tenant_b", source_docs=["x.pdf"])
    with pytest.raises(LookupError):
        collection_admin.delete_documents("unknown_staging", tenant="tenant_a", source_docs=["x.pdf"])


# --- 5. tenant-scoped administrators --------------------------------------------------------------


@pytest.fixture
def admin_api(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    for name in ("API_AUTH_ENABLED", "APP_ENV", "ENTERPRISE_OIDC_ENABLED", "ENTERPRISE_AUTH_ENABLED",
                 "ADMIN_AUTH_ALLOW_INSECURE_DEV"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("ADMIN_AUTH_ENABLED", "true")
    monkeypatch.setenv("ADMIN_AUTH_TOKEN", ADMIN_TOKEN)
    monkeypatch.setenv("ADMIN_AUTH_TENANT_TOKENS", f"{TENANT_A_TOKEN}=tenant_a")
    monkeypatch.setenv("OIDC_COOKIE_SECURE", "false")
    monkeypatch.setenv("INGESTION_REGISTRY_PATH", str(tmp_path / "registry.sqlite3"))
    monkeypatch.setattr(config, "RUNS_DIR", str(tmp_path / "runs"))
    from webapi import ingestion_jobs, main

    for job_id, tenant in (("job-a", "tenant_a"), ("job-b", "tenant_b")):
        ingestion_jobs._save_job({"job_id": job_id, "status": "ok", "created_at": 1.0, "expected_tenant": tenant})
    review = tmp_path / "runs" / "review"
    review.mkdir(parents=True)
    (review / "review_queue.jsonl").write_text(
        "".join(json.dumps({"review_id": f"r-{t}", "tenant_id": t, "status": "open", "priority": "high"}) + "\n"
                for t in ("tenant_a", "tenant_b")), encoding="utf-8")
    return TestClient(main.app), tmp_path


def _audit_actions(tmp_path):
    path = tmp_path / "runs" / "audit" / "events.jsonl"
    if not path.exists():
        return []
    return [json.loads(line).get("action") for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def test_tenant_admin_sees_and_changes_only_its_tenant(admin_api):
    client, tmp_path = admin_api
    headers = {"x-admin-token": TENANT_A_TOKEN}
    jobs = client.get("/admin/ingestion/jobs", headers=headers).json()["jobs"]
    assert [j["job_id"] for j in jobs] == ["job-a"]
    assert client.get("/admin/ingestion/jobs/job-b", headers=headers).status_code == 404
    items = client.get("/admin/review/items", headers=headers).json()["items"]
    assert [i["tenant_id"] for i in items] == ["tenant_a"]
    assert client.get("/admin/review/items?tenant_id=tenant_b", headers=headers).status_code == 403
    denied = client.post("/admin/review/action", headers=headers,
                         json={"review_id": "r-tenant_b", "action_type": "reject_candidate", "tenant_id": "tenant_b"})
    assert denied.status_code == 403
    assert client.post("/admin/ingestion/raw-documents", headers=headers,
                       json={"inputs": ["x.csv"], "expected_tenant": "tenant_b", "collection": "s1"}).status_code == 403
    assert client.post("/admin/ingestion/documents/delete", headers=headers,
                       json={"tenant_id": "tenant_b", "source_docs": ["x.pdf"]}).status_code == 403
    # Installation-wide actions need the installation administrator.
    assert client.get("/admin/synonyms/candidates", headers=headers).status_code == 403
    assert client.post("/admin/logout-all", headers=headers).status_code == 403


def test_installation_admin_sees_every_tenant(admin_api):
    client, _ = admin_api
    headers = {"x-admin-token": ADMIN_TOKEN}
    jobs = client.get("/admin/ingestion/jobs", headers=headers).json()["jobs"]
    assert sorted(j["job_id"] for j in jobs) == ["job-a", "job-b"]
    assert len(client.get("/admin/review/items", headers=headers).json()["items"]) == 2


def test_login_session_keeps_the_scope_and_actions_are_audited(admin_api):
    client, tmp_path = admin_api
    assert client.post("/admin/login", json={"token": "wrong-token"}).status_code == 403
    assert client.post("/admin/login", json={"token": TENANT_A_TOKEN}).status_code == 200
    jobs = client.get("/admin/ingestion/jobs").json()["jobs"]  # the session cookie
    assert [j["job_id"] for j in jobs] == ["job-a"]
    csrf = client.cookies.get("admin_csrf")
    ok = client.post("/admin/review/action", headers={"X-CSRF-Token": csrf},
                     json={"review_id": "r-tenant_a", "action_type": "reject_candidate", "tenant_id": "tenant_a"})
    assert ok.status_code == 200
    actions = _audit_actions(tmp_path)
    assert {"admin_login_failed", "admin_login", "review_action"} <= set(actions)
    raw = (tmp_path / "runs" / "audit" / "events.jsonl").read_text(encoding="utf-8")
    assert TENANT_A_TOKEN not in raw and ADMIN_TOKEN not in raw


def test_tampered_session_scope_is_refused(admin_api):
    from webapi import admin_auth

    client, _ = admin_api
    client.post("/admin/login", json={"token": TENANT_A_TOKEN})
    cookie = client.cookies.get("admin_session")
    sid, issued, expires, _scope, signature = cookie.split(".")
    widened = ".".join([sid, issued, expires, signature])  # drop the scope: would be installation-wide
    client.cookies.clear()
    client.cookies.set("admin_session", widened)
    assert client.get("/admin/ingestion/jobs").status_code in (401, 303)
    assert admin_auth.token_scope(TENANT_A_TOKEN) == (True, frozenset({"tenant_a"}))
    assert admin_auth.token_scope(ADMIN_TOKEN) == (True, None)
    assert admin_auth.token_scope("nope")[0] is False
