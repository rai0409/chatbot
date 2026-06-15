from __future__ import annotations

import json

import pytest
from fastapi import HTTPException

import config
from webapi.admin_auth import require_admin_auth_headers
from webapi import ingestion_jobs, main


SYNTH = "eval/cases/qa_pair_chunks.jsonl"  # existing synthetic, non-customer data


def _admin(monkeypatch, *, enabled=True):
    for var in ("ADMIN_AUTH_ENABLED", "ADMIN_AUTH_TOKEN"):
        monkeypatch.delenv(var, raising=False)
    if enabled:
        monkeypatch.setenv("ADMIN_AUTH_ENABLED", "true")
        monkeypatch.setenv("ADMIN_AUTH_TOKEN", "admin-secret")
    ingestion_jobs.reset()


def _h(tok="admin-secret"):
    return {"X-Admin-Token": tok}


# --- module: dry-run + production refusal ----------------------------------


def test_module_dry_run_no_issues_on_synthetic(monkeypatch):
    ingestion_jobs.reset()
    rec = ingestion_jobs.run_dry_run([SYNTH], collection="pilot_v1")
    assert rec["ok"] is True
    assert rec["status"] == "ok"
    assert rec["mode"] == "dry_run"


def test_module_refuses_production_collection(monkeypatch):
    ingestion_jobs.reset()
    for name in (None, "", "default", config.VECTORSTORE_COLLECTION_NAME):
        assert ingestion_jobs.is_production_collection(name) is True
    with pytest.raises(ValueError):
        ingestion_jobs.run_dry_run([SYNTH], collection="default")


# --- endpoints: admin-gated ------------------------------------------------


def test_endpoints_require_admin(monkeypatch):
    _admin(monkeypatch, enabled=True)
    with pytest.raises(HTTPException) as no_token:
        require_admin_auth_headers({})
    assert no_token.value.status_code == 401
    with pytest.raises(HTTPException) as bad_token:
        require_admin_auth_headers(_h("nope"))
    assert bad_token.value.status_code == 403
    require_admin_auth_headers(_h())
    assert main.ingestion_page().status_code == 200


def test_dry_run_endpoint_and_job_status(monkeypatch):
    _admin(monkeypatch, enabled=True)
    job = main.ingestion_dry_run(
        main.IngestionDryRunRequest(inputs=[SYNTH], collection="pilot_v1")
    )
    assert job["ok"] is True and job["mode"] == "dry_run"
    jid = job["job_id"]
    status = main.ingestion_job_status(jid)
    assert status["job_id"] == jid
    listing = main.ingestion_jobs_list()["jobs"]
    assert any(j["job_id"] == jid for j in listing)


def test_dry_run_endpoint_refuses_production_collection(monkeypatch):
    _admin(monkeypatch, enabled=True)
    with pytest.raises(HTTPException) as excinfo:
        main.ingestion_dry_run(
            main.IngestionDryRunRequest(inputs=[SYNTH], collection="default")
        )
    assert excinfo.value.status_code == 400


def test_dry_run_requires_inputs(monkeypatch):
    _admin(monkeypatch, enabled=True)
    with pytest.raises(HTTPException) as excinfo:
        main.ingestion_dry_run(main.IngestionDryRunRequest(inputs=[]))
    assert excinfo.value.status_code == 400


def test_no_secret_or_raw_doc_in_job_output(monkeypatch):
    _admin(monkeypatch, enabled=True)
    job = main.ingestion_dry_run(
        main.IngestionDryRunRequest(inputs=[SYNTH], collection="pilot_v1")
    )
    blob = json.dumps(job, ensure_ascii=False)
    # job carries counts + safe metadata, not raw document text or secrets
    assert "admin-secret" not in blob
    for forbidden in ("sk-", "Bearer ", "X-Api-Key"):
        assert forbidden not in blob
    assert "issue_counts" in blob


# --- raw document path ingestion (Prompt072) --------------------------------


def _write_raw_csv(path):
    path.write_text("質問,回答\n営業時間は？,9時から17時です\n", encoding="utf-8")


def test_raw_document_endpoint_requires_admin(monkeypatch, tmp_path):
    _admin(monkeypatch, enabled=True)
    with pytest.raises(HTTPException) as no_token:
        require_admin_auth_headers({})
    assert no_token.value.status_code == 401
    with pytest.raises(HTTPException) as bad_token:
        require_admin_auth_headers(_h("nope"))
    assert bad_token.value.status_code == 403
    require_admin_auth_headers(_h())


def test_raw_document_dry_run_converts_and_never_ingests(monkeypatch, tmp_path):
    _admin(monkeypatch, enabled=True)
    raw = tmp_path / "faq.csv"
    _write_raw_csv(raw)
    monkeypatch.setattr(
        ingestion_jobs,
        "_ingest_chunks",
        lambda chunks, collection: (_ for _ in ()).throw(AssertionError("dry-run must not ingest")),
    )

    job = main.ingestion_raw_documents(
        main.RawDocumentIngestionRequest(
            inputs=[str(raw)],
            expected_tenant="tenant_raw",
            collection="pilot_staging_v1",
            execute=False,
        )
    )
    assert job["ok"] is True
    assert job["mode"] == "raw_document_dry_run"
    assert job["vectorstore_mutated"] is False
    assert job["index_mutated"] is False
    assert job["processed_files"] == 1
    assert job["skipped_files"] == 0
    assert job["chunks_generated"] == 1
    assert job["source_type_counts"] == {"csv": 1}
    assert job["expected_tenant"] == "tenant_raw"
    assert job["collection"] == "pilot_staging_v1"
    assert job["files"][0]["file"] == "faq.csv"
    status = main.ingestion_job_status(job["job_id"])
    assert status["chunks_generated"] == 1


def test_raw_document_execute_requires_nonproduction_collection(monkeypatch, tmp_path):
    _admin(monkeypatch, enabled=True)
    raw = tmp_path / "faq.csv"
    _write_raw_csv(raw)
    calls = []
    monkeypatch.setattr(
        ingestion_jobs,
        "_ingest_chunks",
        lambda chunks, collection: calls.append((chunks, collection)),
    )

    with pytest.raises(HTTPException) as excinfo:
        main.ingestion_raw_documents(
            main.RawDocumentIngestionRequest(
                inputs=[str(raw)],
                expected_tenant="tenant_raw",
                collection="default",
                execute=True,
            )
        )

    assert excinfo.value.status_code == 400
    assert calls == []


def test_raw_document_execute_imports_only_staging_collection(monkeypatch, tmp_path):
    _admin(monkeypatch, enabled=True)
    raw = tmp_path / "faq.csv"
    _write_raw_csv(raw)
    calls = []

    def _fake_ingest(chunks, collection):
        calls.append((chunks, collection))
        return {
            "ingested": len(chunks),
            "skipped": 0,
            "embedding_fingerprint": {"embed_provider": "local", "embed_model": "test"},
        }

    monkeypatch.setattr(ingestion_jobs, "_ingest_chunks", _fake_ingest)
    job = main.ingestion_raw_documents(
        main.RawDocumentIngestionRequest(
            inputs=[str(raw)],
            expected_tenant="tenant_raw",
            collection="pilot_staging_v1",
            execute=True,
        )
    )

    assert job["ok"] is True
    assert job["mode"] == "raw_document_execute"
    assert job["vectorstore_mutated"] is True
    assert job["index_mutated"] is False
    assert job["ingested_chunks"] == 1
    assert len(calls) == 1
    assert calls[0][1] == "pilot_staging_v1"
    assert calls[0][1] not in ("", "default", config.VECTORSTORE_COLLECTION_NAME)


def test_raw_document_unsupported_type_is_safe_warning(monkeypatch, tmp_path):
    _admin(monkeypatch, enabled=True)
    raw = tmp_path / "notes.txt"
    raw.write_text("raw private text must not be echoed", encoding="utf-8")
    monkeypatch.setattr(
        ingestion_jobs,
        "_ingest_chunks",
        lambda chunks, collection: (_ for _ in ()).throw(AssertionError("unsupported must not ingest")),
    )

    job = main.ingestion_raw_documents(
        main.RawDocumentIngestionRequest(
            inputs=[str(raw)],
            expected_tenant="tenant_raw",
            collection="pilot_staging_v1",
            execute=True,
        )
    )

    blob = json.dumps(job, ensure_ascii=False)
    assert job["ok"] is False
    assert job["vectorstore_mutated"] is False
    assert job["processed_files"] == 0
    assert job["skipped_files"] == 1
    assert job["warnings"][0]["reason"] == "unsupported_type"
    assert "raw private text" not in blob
    assert "admin-secret" not in blob


def test_admin_ingestion_ui_distinguishes_jsonl_and_raw_document_flows(monkeypatch):
    _admin(monkeypatch, enabled=True)
    html = main.ingestion_page().body.decode("utf-8")

    assert "/admin/ingestion/dry-run" in html
    assert "/admin/ingestion/raw-documents" in html
    assert "ドライランのみ" in html
    assert "ステージングベクトルストアへ実取込" in html
    assert "PDF / DOCX / XLSX / CSV / PPTX" in html
