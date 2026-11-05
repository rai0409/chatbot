"""PR A / U1: the tenant folder of an upload can never leave the upload root.
Synthetic data only."""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from fastapi import HTTPException

from webapi.routers import ingestion


class _StubRequest:
    """Direct handler call: no FastAPI request, so the admin scope is None."""

    def __init__(self, body: bytes):
        self._body = body

    async def stream(self):
        yield self._body


def _upload(tmp_path, monkeypatch, tenant):
    root = tmp_path / "ingest"
    root.mkdir(exist_ok=True)
    monkeypatch.setattr(ingestion.ingestion_jobs, "allowed_ingest_roots", lambda: [root.resolve()])
    monkeypatch.delenv("INGEST_UPLOAD_DIR", raising=False)
    result = asyncio.run(
        ingestion.ingestion_upload(_StubRequest(b"%PDF-1.4 synthetic"), filename="a.pdf", tenant=tenant)
    )
    return root.resolve() / "uploads", Path(result["path"])


@pytest.mark.parametrize("tenant", ["..", ".", "...", "../..", "a/../..", "..\\.."])
def test_dot_tenants_stay_inside_upload_root(tmp_path, monkeypatch, tenant):
    try:
        upload_root, saved = _upload(tmp_path, monkeypatch, tenant)
    except HTTPException as exc:
        assert exc.status_code == 400
        return
    assert saved.resolve().is_relative_to(upload_root)
    # the tenant folder is a real child, never the root itself or its parent
    assert saved.resolve().parent.parent.parent == upload_root


def test_ordinary_tenant_is_unchanged(tmp_path, monkeypatch):
    upload_root, saved = _upload(tmp_path, monkeypatch, "tenant-a")
    assert saved.parent.parent.name == "tenant-a"
    assert saved.resolve().is_relative_to(upload_root)


# --- U2 / U3 / U5 / U6 / U8 ---------------------------------------------------------------------

import io
import os
import time
import zipfile



def _zip_bytes(names, payload=b"x"):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name in names:
            zf.writestr(name, payload)
    return buf.getvalue()


def _put(tmp_path, monkeypatch, body, filename="a.pdf", tenant="t1"):
    root = tmp_path / "ingest"
    root.mkdir(exist_ok=True)
    monkeypatch.setattr(ingestion.ingestion_jobs, "allowed_ingest_roots", lambda: [root.resolve()])
    monkeypatch.delenv("INGEST_UPLOAD_DIR", raising=False)
    return asyncio.run(ingestion.ingestion_upload(_StubRequest(body), filename=filename, tenant=tenant))


def _code(fn):
    try:
        fn()
    except HTTPException as exc:
        return exc.status_code
    return 200


def test_u2_pdf_needs_full_signature(tmp_path, monkeypatch):
    assert _code(lambda: _put(tmp_path, monkeypatch, b"%PDFxxxx")) == 400
    assert _code(lambda: _put(tmp_path, monkeypatch, b"%PDF-1.7 ok")) == 200


def test_u2_office_files_must_be_real_packages(tmp_path, monkeypatch):
    bad = b"PK\x03\x04 not really a zip"
    assert _code(lambda: _put(tmp_path, monkeypatch, bad, "a.docx")) == 400
    wrong_kind = _zip_bytes(["[Content_Types].xml", "xl/workbook.xml"])
    assert _code(lambda: _put(tmp_path, monkeypatch, wrong_kind, "a.docx")) == 400
    good = _zip_bytes(["[Content_Types].xml", "word/document.xml"])
    assert _code(lambda: _put(tmp_path, monkeypatch, good, "a.docx")) == 200


def test_u2_zip_entry_count_and_traversal_names(tmp_path, monkeypatch):
    monkeypatch.setenv("INGEST_UPLOAD_MAX_ZIP_ENTRIES", "5")
    many = _zip_bytes(["[Content_Types].xml", "word/document.xml"] + [f"word/p{i}.xml" for i in range(10)])
    assert _code(lambda: _put(tmp_path, monkeypatch, many, "a.docx")) == 400
    monkeypatch.delenv("INGEST_UPLOAD_MAX_ZIP_ENTRIES")
    evil = _zip_bytes(["[Content_Types].xml", "word/document.xml", "../evil.xml"])
    assert _code(lambda: _put(tmp_path, monkeypatch, evil, "a.docx")) == 400


def test_u2_zip_bomb_is_refused(tmp_path, monkeypatch):
    monkeypatch.setenv("INGEST_UPLOAD_MAX_UNCOMPRESSED_BYTES", "100000")
    bomb = _zip_bytes(["[Content_Types].xml", "word/document.xml"], payload=b"0" * 2_000_000)
    assert _code(lambda: _put(tmp_path, monkeypatch, bomb, "a.docx")) == 400


def test_u2_csv_must_be_text(tmp_path, monkeypatch):
    assert _code(lambda: _put(tmp_path, monkeypatch, b"a,b\x00\x01\n", "a.csv")) == 400
    assert _code(lambda: _put(tmp_path, monkeypatch, "名前,値\n山田太郎,1\n".encode(), "a.csv")) == 200


def test_u3_tenant_quota(tmp_path, monkeypatch):
    monkeypatch.setenv("INGEST_UPLOAD_TENANT_QUOTA_BYTES", "100")
    assert _code(lambda: _put(tmp_path, monkeypatch, b"%PDF-" + b"0" * 60)) == 200
    assert _code(lambda: _put(tmp_path, monkeypatch, b"%PDF-" + b"0" * 60)) == 413
    # another tenant has its own quota
    assert _code(lambda: _put(tmp_path, monkeypatch, b"%PDF-" + b"0" * 60, tenant="t2")) == 200


def test_u3_concurrent_uploads_are_limited(tmp_path, monkeypatch):
    monkeypatch.setenv("INGEST_UPLOAD_MAX_CONCURRENT", "1")
    ingestion._UPLOAD_ACTIVE["n"] = 1  # one upload already running
    try:
        assert _code(lambda: _put(tmp_path, monkeypatch, b"%PDF-1.7")) == 429
    finally:
        ingestion._UPLOAD_ACTIVE["n"] = 0
    assert _code(lambda: _put(tmp_path, monkeypatch, b"%PDF-1.7")) == 200
    assert ingestion._UPLOAD_ACTIVE["n"] == 0  # released after success and after failure
    assert _code(lambda: _put(tmp_path, monkeypatch, b"nope")) == 400
    assert ingestion._UPLOAD_ACTIVE["n"] == 0


def test_u5_returned_path_has_no_host_prefix_and_round_trips(tmp_path, monkeypatch):
    base = tmp_path / "app"
    root = base / "data"
    root.mkdir(parents=True)
    monkeypatch.setattr(ingestion.config, "BASE_DIR", str(base))
    monkeypatch.setattr(ingestion.ingestion_jobs, "allowed_ingest_roots", lambda: [root.resolve()])
    monkeypatch.delenv("INGEST_UPLOAD_DIR", raising=False)
    result = asyncio.run(ingestion.ingestion_upload(_StubRequest(b"%PDF-1.7"), filename="a.pdf", tenant="t1"))
    assert not Path(result["path"]).is_absolute()
    assert str(tmp_path) not in result["path"]
    assert (base / result["path"]).is_file()  # relative inputs resolve against BASE_DIR


def test_u6_upload_is_audited(tmp_path, monkeypatch):
    events = []
    monkeypatch.setattr(ingestion, "_audit_admin", lambda scope, action, **f: events.append((action, f)))
    result = _put(tmp_path, monkeypatch, b"%PDF-1.7")
    assert events and events[0][0] == "ingestion_upload"
    fields = events[0][1]
    assert fields["tenant_id"] == "t1" and fields["bytes"] == result["bytes"] and fields["sha256"] == result["sha256"]
    assert "path" not in fields


def test_u8_stale_part_files_are_pruned(tmp_path):
    day = tmp_path / "t1" / "20260101"
    day.mkdir(parents=True)
    old = day / ".0123456789ab_a.pdf.part"
    fresh = day / ".aaaaaaaaaaaa_b.pdf.part"
    old.write_bytes(b"x")
    fresh.write_bytes(b"x")
    past = time.time() - 7200
    os.utime(old, (past, past))
    ingestion._UPLOAD_PRUNE["last"] = 0.0
    ingestion._prune_old_uploads(tmp_path)
    assert not old.exists() and fresh.exists()


def test_u3_default_tenant_quota_is_bounded_but_can_be_turned_off(tmp_path, monkeypatch):
    monkeypatch.delenv("INGEST_UPLOAD_TENANT_QUOTA_BYTES", raising=False)
    assert ingestion._tenant_quota_bytes() == 5 * 1024 ** 3
    monkeypatch.setenv("INGEST_UPLOAD_TENANT_QUOTA_BYTES", "0")
    assert ingestion._tenant_quota_bytes() == 0
