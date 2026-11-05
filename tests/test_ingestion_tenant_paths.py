"""PR A / U10, U11: an admin cannot ingest another tenant's uploaded file; audit follows validation. Synthetic data only."""
from __future__ import annotations

import pytest
from fastapi import HTTPException

from webapi.routers import ingestion


@pytest.fixture
def uploads(monkeypatch, tmp_path):
    root = tmp_path / "ingest"
    (root / "uploads" / "tenant_a" / "20261101").mkdir(parents=True)
    (root / "uploads" / "tenant_b" / "20261101").mkdir(parents=True)
    (root / "docs").mkdir()
    for path in (root / "uploads" / "tenant_a" / "20261101" / "aaaaaaaaaaaa_a.pdf",
                 root / "uploads" / "tenant_b" / "20261101" / "bbbbbbbbbbbb_b.pdf",
                 root / "docs" / "shared.pdf"):
        path.write_bytes(b"%PDF-1.7")
    monkeypatch.setattr(ingestion.ingestion_jobs, "allowed_ingest_roots", lambda: [root.resolve()])
    monkeypatch.delenv("INGEST_UPLOAD_DIR", raising=False)
    monkeypatch.setattr(ingestion.ingestion_jobs, "run_raw_document_job", lambda *a, **k: {"ok": True})
    monkeypatch.setattr(ingestion.ingestion_jobs, "run_dry_run", lambda *a, **k: {"ok": True})
    monkeypatch.setattr(ingestion.ingestion_jobs, "is_production_collection", lambda c: c == "prod")
    return root


def _raw(inputs, tenant="tenant_a", collection="staging_x", execute=False):
    req = ingestion.RawDocumentIngestionRequest(inputs=inputs, expected_tenant=tenant, collection=collection, execute=execute)
    return ingestion.ingestion_raw_documents(req, request=None)


def test_own_upload_folder_is_accepted(uploads):
    mine = str(uploads / "uploads" / "tenant_a" / "20261101" / "aaaaaaaaaaaa_a.pdf")
    assert _raw([mine]) == {"ok": True}


def test_another_tenants_upload_is_refused(uploads):
    theirs = str(uploads / "uploads" / "tenant_b" / "20261101" / "bbbbbbbbbbbb_b.pdf")
    with pytest.raises(HTTPException) as exc:
        _raw([theirs])
    assert exc.value.status_code == 403


def test_the_same_check_applies_to_the_dry_run(uploads):
    theirs = str(uploads / "uploads" / "tenant_b" / "20261101" / "bbbbbbbbbbbb_b.pdf")
    req = ingestion.IngestionDryRunRequest(inputs=[theirs], expected_tenant="tenant_a")
    with pytest.raises(HTTPException) as exc:
        ingestion.ingestion_dry_run(req, request=None)
    assert exc.value.status_code == 403


def test_files_outside_the_upload_folder_are_not_tenant_checked(uploads):
    assert _raw([str(uploads / "docs" / "shared.pdf")]) == {"ok": True}


def test_a_dotted_path_into_another_tenants_folder_is_refused(uploads):
    sneaky = str(uploads / "uploads" / "tenant_a" / ".." / "tenant_b" / "20261101" / "bbbbbbbbbbbb_b.pdf")
    with pytest.raises(HTTPException) as exc:
        _raw([sneaky])
    assert exc.value.status_code == 403


def test_a_refused_request_is_not_audited_as_an_execution(uploads, monkeypatch):
    events = []
    monkeypatch.setattr(ingestion, "_audit_admin", lambda scope, action, **f: events.append(action))
    with pytest.raises(HTTPException):
        _raw(["x.pdf"], collection="prod", execute=True)  # production collection: refused
    theirs = str(uploads / "uploads" / "tenant_b" / "20261101" / "bbbbbbbbbbbb_b.pdf")
    with pytest.raises(HTTPException):
        _raw([theirs], execute=True)
    assert events == []
    mine = str(uploads / "uploads" / "tenant_a" / "20261101" / "aaaaaaaaaaaa_a.pdf")
    _raw([mine], execute=True)
    assert events == ["ingestion_execute"]
